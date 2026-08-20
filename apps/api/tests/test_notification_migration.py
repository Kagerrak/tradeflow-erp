from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

_ALEMBIC_INI = str(Path(__file__).resolve().parents[1] / "alembic.ini")


async def _has_table(connection: AsyncConnection, table_name: str) -> bool:
    result = await connection.execute(
        text("SELECT 1 FROM pg_tables WHERE schemaname = 'public' AND tablename = :table_name"),
        {"table_name": table_name},
    )
    return result.scalar_one_or_none() == 1


async def _has_index(connection: AsyncConnection, index_name: str) -> bool:
    result = await connection.execute(
        text(
            """
            SELECT 1 FROM pg_indexes
            WHERE schemaname = 'public' AND indexname = :index_name
            """
        ),
        {"index_name": index_name},
    )
    return result.scalar_one_or_none() == 1


async def _has_function(connection: AsyncConnection, function_name: str) -> bool:
    result = await connection.execute(
        text(
            """
            SELECT 1 FROM pg_proc p
            JOIN pg_namespace n ON p.pronamespace = n.oid
            WHERE n.nspname = 'public' AND p.proname = :function_name
            """
        ),
        {"function_name": function_name},
    )
    return result.scalar_one_or_none() == 1


async def _has_trigger(connection: AsyncConnection, trigger_name: str, table_name: str) -> bool:
    result = await connection.execute(
        text(
            """
            SELECT 1 FROM pg_trigger t
            JOIN pg_class c ON t.tgrelid = c.oid
            JOIN pg_namespace n ON c.relnamespace = n.oid
            WHERE n.nspname = 'public'
              AND c.relname = :table_name
              AND t.tgname = :trigger_name
            """
        ),
        {"trigger_name": trigger_name, "table_name": table_name},
    )
    return result.scalar_one_or_none() == 1


@pytest.mark.asyncio
async def test_migration_creates_notification_schema(postgres_url: str) -> None:
    engine = create_async_engine(postgres_url)
    async with engine.begin() as connection:
        assert await _has_table(connection, "device_registrations")
        assert await _has_table(connection, "notification_preferences")
        assert await _has_table(connection, "operational_notifications")
        assert await _has_table(connection, "notification_deliveries")
        assert await _has_table(connection, "notification_read_events")
        assert await _has_table(connection, "notification_effect_events")
        assert await _has_index(connection, "ix_device_registrations_user_active")
        assert await _has_index(connection, "ix_operational_notifications_recipient_status")
        assert await _has_index(connection, "ix_operational_notifications_recipient_created")
        assert await _has_index(connection, "ix_operational_notifications_source_event")
        assert await _has_index(connection, "ix_notification_deliveries_notification")
        assert await _has_index(connection, "ix_notification_read_events_notification")
        assert await _has_index(connection, "ix_notification_effect_events_notification")
        assert await _has_function(connection, "protect_operational_notifications")
        assert await _has_function(connection, "protect_notification_deliveries")
        assert await _has_function(connection, "protect_notification_read_events")
        assert await _has_function(connection, "protect_notification_effect_events")
        assert await _has_function(connection, "protect_device_registrations")
        assert await _has_function(connection, "protect_notification_preferences")
        assert await _has_trigger(
            connection, "trg_operational_notifications_immutable", "operational_notifications"
        )
        assert await _has_trigger(
            connection, "trg_notification_deliveries_immutable", "notification_deliveries"
        )
        assert await _has_trigger(
            connection, "trg_notification_read_events_immutable", "notification_read_events"
        )
        assert await _has_trigger(
            connection, "trg_notification_effect_events_immutable", "notification_effect_events"
        )
        assert await _has_trigger(
            connection, "trg_device_registrations_immutable", "device_registrations"
        )
        assert await _has_trigger(
            connection, "trg_notification_preferences_immutable", "notification_preferences"
        )
    await engine.dispose()


@pytest.mark.asyncio
async def test_notification_immutability_trigger_blocks_identity_updates(
    postgres_url: str,
) -> None:
    engine = create_async_engine(postgres_url)
    async with engine.begin() as connection:
        company_id = uuid4()
        branch_id = uuid4()
        warehouse_id = uuid4()
        user_subject = "migration-test-user"
        notification_id = uuid4()
        await connection.execute(
            text(
                "INSERT INTO companies (company_id, singleton_key, code, name, base_currency) "
                "VALUES (:company_id, 'tradeflow', 'MIG', 'Migration Co', 'PHP')"
            ),
            {"company_id": company_id},
        )
        await connection.execute(
            text(
                "INSERT INTO branches (branch_id, company_id, code, name) "
                "VALUES (:branch_id, :company_id, 'MIG-BR', 'Migration Branch')"
            ),
            {"branch_id": branch_id, "company_id": company_id},
        )
        await connection.execute(
            text(
                "INSERT INTO warehouses (warehouse_id, branch_id, code, name) "
                "VALUES (:warehouse_id, :branch_id, 'MIG-WH', 'Migration Warehouse')"
            ),
            {"warehouse_id": warehouse_id, "branch_id": branch_id},
        )
        await connection.execute(
            text("INSERT INTO users (subject, display_name) VALUES (:subject, 'Test User')"),
            {"subject": user_subject},
        )
        await connection.execute(
            text(
                "INSERT INTO operational_notifications ("
                "notification_id, source_event_id, source_type, source_id, "
                "recipient_subject, notification_type, title, body, deep_link_path, "
                "deep_link_token, branch_id, warehouse_id, status, correlation_id"
                ") VALUES (:id, NULL, 'test', :source_id, :recipient, 'test_type', "
                "'Title', 'Body', '/test', 'token', :branch_id, :warehouse_id, 'pending', 'corr')"
            ),
            {
                "id": notification_id,
                "source_id": uuid4(),
                "recipient": user_subject,
                "branch_id": branch_id,
                "warehouse_id": warehouse_id,
            },
        )

        with pytest.raises(DBAPIError):
            await connection.execute(
                text(
                    "UPDATE operational_notifications SET title = 'Hacked' "
                    "WHERE notification_id = :id"
                ),
                {"id": notification_id},
            )

        with pytest.raises(DBAPIError):
            await connection.execute(
                text("DELETE FROM operational_notifications WHERE notification_id = :id"),
                {"id": notification_id},
            )
    await engine.dispose()


@pytest.mark.asyncio
async def test_notification_migration_downgrade_and_reupgrade_is_safe(postgres_url: str) -> None:
    config = Config(_ALEMBIC_INI)
    config.set_main_option("sqlalchemy.url", postgres_url)

    await asyncio.to_thread(command.downgrade, config, "base")
    await asyncio.to_thread(command.upgrade, config, "head")

    engine = create_async_engine(postgres_url)
    async with engine.begin() as connection:
        assert await _has_table(connection, "operational_notifications")
        assert await _has_function(connection, "protect_operational_notifications")
    await engine.dispose()
