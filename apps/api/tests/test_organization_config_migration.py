from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

_ALEMBIC_INI = str(Path(__file__).resolve().parents[1] / "alembic.ini")


async def _has_table(connection: AsyncConnection, table_name: str) -> bool:
    result = await connection.execute(
        text("SELECT 1 FROM pg_tables WHERE schemaname = 'public' AND tablename = :table_name"),
        {"table_name": table_name},
    )
    return result.scalar_one_or_none() == 1


async def _has_column(connection: AsyncConnection, table_name: str, column_name: str) -> bool:
    result = await connection.execute(
        text(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = :table_name
              AND column_name = :column_name
            """
        ),
        {"table_name": table_name, "column_name": column_name},
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


async def _has_constraint(
    connection: AsyncConnection, table_name: str, constraint_name: str
) -> bool:
    result = await connection.execute(
        text(
            """
            SELECT 1 FROM pg_constraint con
            JOIN pg_class c ON con.conrelid = c.oid
            JOIN pg_namespace n ON c.relnamespace = n.oid
            WHERE n.nspname = 'public'
              AND c.relname = :table_name
              AND con.conname = :constraint_name
            """
        ),
        {"table_name": table_name, "constraint_name": constraint_name},
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


@pytest.mark.asyncio
async def test_migration_0019_creates_operational_policy_schema(
    postgres_url: str,
) -> None:
    engine = create_async_engine(postgres_url)
    async with engine.begin() as connection:
        assert await _has_column(connection, "companies", "timezone")
        assert await _has_column(connection, "branches", "timezone")
        assert await _has_column(connection, "document_series", "version")
        assert await _has_table(connection, "document_templates")
        assert await _has_index(connection, "uq_document_template_company_type_version")
        assert await _has_index(connection, "uq_document_template_branch_type_version")
        assert await _has_constraint(connection, "companies", "ck_companies_timezone_not_empty")
        assert await _has_constraint(connection, "branches", "ck_branches_timezone_not_empty")
        assert await _has_constraint(
            connection, "document_templates", "ck_document_templates_version_positive"
        )
        assert await _has_constraint(
            connection, "document_series", "ck_document_series_version_positive"
        )
        assert await _has_function(connection, "prevent_base_currency_change_with_postings")
        assert await _has_trigger(connection, "companies_base_currency_immutable", "companies")
        assert await _has_function(connection, "protect_document_series")
        assert await _has_function(connection, "validate_document_series_audit")
    await engine.dispose()


@pytest.mark.asyncio
async def test_migration_downgrade_base_and_reupgrade_is_safe(
    postgres_url: str,
) -> None:
    config = Config(_ALEMBIC_INI)
    config.set_main_option("sqlalchemy.url", postgres_url)

    await asyncio.to_thread(command.downgrade, config, "base")
    await asyncio.to_thread(command.upgrade, config, "head")

    engine = create_async_engine(postgres_url)
    async with engine.begin() as connection:
        assert await _has_column(connection, "companies", "timezone")
        assert await _has_table(connection, "document_templates")
        assert await _has_function(connection, "prevent_base_currency_change_with_postings")
        assert await _has_function(connection, "protect_document_series")
    await engine.dispose()
