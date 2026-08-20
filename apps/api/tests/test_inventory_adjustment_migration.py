from __future__ import annotations

import asyncio
import os
from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from tradeflow_api.config import Settings


@pytest.fixture
def migration_database_url(postgres_url: str) -> Iterator[str]:
    base = postgres_url.rsplit("/", 1)[0]
    url = f"{base}/tradeflow_adjustment_migration_test"
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


async def _reset_migration_database(admin_url: str, target_url: str) -> None:
    engine = create_async_engine(admin_url)
    async with engine.connect() as connection:
        await connection.execution_options(isolation_level="AUTOCOMMIT")
        await connection.execute(
            text(
                "SELECT pg_terminate_backend(pid) "
                "FROM pg_stat_activity "
                "WHERE datname = 'tradeflow_adjustment_migration_test' AND pid <> pg_backend_pid()"
            )
        )
        await connection.execute(
            text("DROP DATABASE IF EXISTS tradeflow_adjustment_migration_test")
        )
        await connection.execute(text("CREATE DATABASE tradeflow_adjustment_migration_test"))
    await engine.dispose()


@pytest.fixture(autouse=True)
def fresh_migration_database(
    migration_database_url: str,
    migration_alembic_config: Config,
) -> None:
    admin_url = migration_database_url.rsplit("/", 1)[0] + "/postgres"
    asyncio.run(_reset_migration_database(admin_url, migration_database_url))
    command.upgrade(migration_alembic_config, "head")


async def _seed_minimum_adjustment_history(database_url: str) -> None:
    engine = create_async_engine(database_url)
    company_id = uuid4()
    branch_id = uuid4()
    warehouse_id = uuid4()
    sku_id = uuid4()
    product_id = uuid4()
    location_id = uuid4()
    adjustment_id = uuid4()

    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO companies (
                  company_id, singleton_key, code, name, base_currency, timezone)
                VALUES (:company_id, 'tradeflow', 'TF', 'Test', 'PHP', 'UTC')
                """
            ),
            {"company_id": company_id},
        )
        await connection.execute(
            text(
                """
                INSERT INTO branches (branch_id, company_id, code, name, timezone)
                VALUES (:branch_id, :company_id, 'MNL', 'Manila', 'UTC')
                """
            ),
            {"branch_id": branch_id, "company_id": company_id},
        )
        await connection.execute(
            text(
                """
                INSERT INTO warehouses (warehouse_id, branch_id, code, name)
                VALUES (:warehouse_id, :branch_id, 'MNL-01', 'Manila DC')
                """
            ),
            {"warehouse_id": warehouse_id, "branch_id": branch_id},
        )
        await connection.execute(
            text(
                """
                INSERT INTO users (subject, display_name, is_active, is_operations_administrator)
                VALUES ('test-user', 'Test User', true, false)
                """
            )
        )
        await connection.execute(
            text(
                """
                INSERT INTO products (product_id, code, name, created_by)
                VALUES (:product_id, :code, 'Product', 'test-user')
                """
            ),
            {"product_id": product_id, "code": f"PROD-{uuid4()}"},
        )
        await connection.execute(
            text(
                """
                INSERT INTO skus (sku_id, product_id, code, name, base_stocking_unit,
                  tracking_policy, expiration_control, created_by)
                VALUES (
                  :sku_id, :product_id, :sku_code, 'SKU', 'EA',
                  'untracked', false, 'test-user')
                """
            ),
            {"sku_id": sku_id, "product_id": product_id, "sku_code": f"SKU-{uuid4()}"},
        )
        await connection.execute(
            text(
                """
                INSERT INTO warehouse_stock_locations (location_id, warehouse_id, code, name,
                  custody, is_active, created_by)
                VALUES (
                  :location_id, :warehouse_id, 'LOC', 'Location',
                  'available', true, 'test-user')
                """
            ),
            {"location_id": location_id, "warehouse_id": warehouse_id},
        )
        await connection.execute(
            text(
                """
                INSERT INTO inventory_adjustments (adjustment_id, sku_id, warehouse_id,
                  location_id, kind, quantity_base, unit_cost, value_delta, base_currency,
                  reason, source_reference, status, version, requested_by, requested_at,
                  correlation_id, idempotency_key)
                VALUES (:adjustment_id, :sku_id, :warehouse_id, :location_id, 'surplus',
                  10.000000, 5.000000, 50.000000, 'PHP', 'Reason', 'REF',
                  'pending_authorization', 1, 'test-user', now(), 'corr', :idempotency_key)
                """
            ),
            {
                "adjustment_id": adjustment_id,
                "sku_id": sku_id,
                "warehouse_id": warehouse_id,
                "location_id": location_id,
                "idempotency_key": f"test-adjustment-header-{uuid4()}",
            },
        )

    await engine.dispose()


@pytest.mark.asyncio
async def test_inventory_adjustment_migration_round_trip(
    migration_database_url: str,
    migration_alembic_config: Config,
) -> None:
    await _seed_minimum_adjustment_history(migration_database_url)

    # Downgrade is blocked while immutable adjustment history exists.
    with pytest.raises(Exception, match="Cannot downgrade 0022"):
        await asyncio.to_thread(command.downgrade, migration_alembic_config, "0018")

    engine = create_async_engine(migration_database_url)
    async with engine.begin() as connection:
        await connection.execute(text("TRUNCATE TABLE inventory_adjustment_authorizations CASCADE"))
        await connection.execute(text("TRUNCATE TABLE inventory_adjustments CASCADE"))
        await connection.execute(
            text("DELETE FROM stock_movements WHERE movement_type = 'inventory_adjustment'")
        )
    await engine.dispose()

    # Empty downgrade to the transfer/notification merge baseline and re-upgrade succeeds.
    await asyncio.to_thread(command.downgrade, migration_alembic_config, "0018")
    await asyncio.to_thread(command.upgrade, migration_alembic_config, "head")

    async with engine.connect() as connection:
        version = await connection.scalar(text("SELECT version_num FROM alembic_version"))
    await engine.dispose()
    assert version == "0022"


async def _insert_minimum_adjustment(
    connection: object,
    company_id: object,
    branch_id: object,
    warehouse_id: object,
    sku_id: object,
    product_id: object,
    location_id: object,
    adjustment_id: object,
    idempotency_key: str,
) -> None:
    await connection.execute(
        text(
            """
            INSERT INTO companies (company_id, singleton_key, code, name, base_currency, timezone)
            VALUES (:company_id, 'tradeflow', 'TF', 'Test', 'PHP', 'UTC')
            """
        ),
        {"company_id": company_id},
    )
    await connection.execute(
        text(
            """
            INSERT INTO branches (branch_id, company_id, code, name, timezone)
            VALUES (:branch_id, :company_id, 'MNL', 'Manila', 'UTC')
            """
        ),
        {"branch_id": branch_id, "company_id": company_id},
    )
    await connection.execute(
        text(
            """
            INSERT INTO warehouses (warehouse_id, branch_id, code, name)
            VALUES (:warehouse_id, :branch_id, 'MNL-01', 'Manila DC')
            """
        ),
        {"warehouse_id": warehouse_id, "branch_id": branch_id},
    )
    await connection.execute(
        text(
            """
            INSERT INTO users (subject, display_name, is_active, is_operations_administrator)
            VALUES ('test-user', 'Test User', true, false)
            """
        )
    )
    await connection.execute(
        text(
            """
            INSERT INTO products (product_id, code, name, created_by)
            VALUES (:product_id, :code, 'Product', 'test-user')
            """
        ),
        {"product_id": product_id, "code": f"PROD-{uuid4()}"},
    )
    await connection.execute(
        text(
            """
            INSERT INTO skus (sku_id, product_id, code, name, base_stocking_unit,
              tracking_policy, expiration_control, created_by)
            VALUES (:sku_id, :product_id, :sku_code, 'SKU', 'EA', 'untracked', false, 'test-user')
            """
        ),
        {"sku_id": sku_id, "product_id": product_id, "sku_code": f"SKU-{uuid4()}"},
    )
    await connection.execute(
        text(
            """
            INSERT INTO warehouse_stock_locations (location_id, warehouse_id, code, name,
              custody, is_active, created_by)
            VALUES (:location_id, :warehouse_id, 'LOC', 'Location', 'available', true, 'test-user')
            """
        ),
        {"location_id": location_id, "warehouse_id": warehouse_id},
    )
    await connection.execute(
        text(
            """
            INSERT INTO inventory_adjustments (adjustment_id, sku_id, warehouse_id,
              location_id, kind, quantity_base, unit_cost, value_delta, base_currency,
              reason, source_reference, status, version, requested_by, requested_at,
              correlation_id, idempotency_key)
            VALUES (:adjustment_id, :sku_id, :warehouse_id, :location_id, 'surplus',
              10.000000, 5.000000, 50.000000, 'PHP', 'Reason', 'REF',
              'pending_authorization', 1, 'test-user', now(), 'corr', :idempotency_key)
            """
        ),
        {
            "adjustment_id": adjustment_id,
            "sku_id": sku_id,
            "warehouse_id": warehouse_id,
            "location_id": location_id,
            "idempotency_key": idempotency_key,
        },
    )


@pytest.mark.asyncio
async def test_inventory_adjustment_immutability_trigger_rejects_update(
    migration_database_url: str,
) -> None:
    engine = create_async_engine(migration_database_url)
    async with engine.begin() as connection:
        adjustment_id = uuid4()
        await _insert_minimum_adjustment(
            connection,
            uuid4(),
            uuid4(),
            uuid4(),
            uuid4(),
            uuid4(),
            uuid4(),
            adjustment_id,
            f"test-immutability-update-{uuid4()}",
        )

        with pytest.raises(Exception, match="Inventory Adjustment history is immutable"):
            await connection.execute(
                text(
                    "UPDATE inventory_adjustments SET reason = 'Changed' "
                    "WHERE adjustment_id = :adjustment_id"
                ),
                {"adjustment_id": adjustment_id},
            )

    await engine.dispose()


@pytest.mark.asyncio
async def test_inventory_adjustment_immutability_trigger_rejects_delete(
    migration_database_url: str,
) -> None:
    engine = create_async_engine(migration_database_url)
    async with engine.begin() as connection:
        adjustment_id = uuid4()
        await _insert_minimum_adjustment(
            connection,
            uuid4(),
            uuid4(),
            uuid4(),
            uuid4(),
            uuid4(),
            uuid4(),
            adjustment_id,
            f"test-immutability-delete-{uuid4()}",
        )

        with pytest.raises(Exception, match="Inventory Adjustment history is immutable"):
            await connection.execute(
                text("DELETE FROM inventory_adjustments WHERE adjustment_id = :adjustment_id"),
                {"adjustment_id": adjustment_id},
            )

    await engine.dispose()
