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
async def test_migration_creates_expense_schema(postgres_url: str) -> None:
    engine = create_async_engine(postgres_url)
    async with engine.begin() as connection:
        assert await _has_table(connection, "expense_categories")
        assert await _has_table(connection, "expense_policies")
        assert await _has_index(connection, "uq_expense_categories_active_code")
        assert await _has_index(connection, "uq_expense_policies_version")
        assert await _has_index(connection, "uq_expense_policies_active_code")
        assert await _has_function(connection, "protect_published_expense_categories")
        assert await _has_function(connection, "prevent_overlapping_expense_categories")
        assert await _has_function(connection, "protect_published_expense_policies")
        assert await _has_function(connection, "prevent_overlapping_expense_policies")
        assert await _has_trigger(
            connection, "trg_protect_published_expense_categories", "expense_categories"
        )
        assert await _has_trigger(
            connection, "trg_prevent_overlapping_expense_categories", "expense_categories"
        )
        assert await _has_trigger(
            connection, "trg_protect_published_expense_policies", "expense_policies"
        )
        assert await _has_trigger(
            connection, "trg_prevent_overlapping_expense_policies", "expense_policies"
        )
    await engine.dispose()


@pytest.mark.asyncio
async def test_expense_category_overlap_trigger_blocks_conflicts(postgres_url: str) -> None:
    engine = create_async_engine(postgres_url)
    async with engine.begin() as connection:
        company_id = uuid4()
        user_subject = "migration-test-user"
        await connection.execute(
            text(
                "INSERT INTO companies (company_id, singleton_key, code, name, base_currency) "
                "VALUES (:company_id, 'tradeflow', 'MIG', 'Migration Co', 'PHP')"
            ),
            {"company_id": company_id},
        )
        await connection.execute(
            text("INSERT INTO users (subject, display_name) VALUES (:subject, 'Test User')"),
            {"subject": user_subject},
        )

        version_1_id = uuid4()
        await connection.execute(
            text(
                "INSERT INTO expense_categories ("
                "expense_category_version_id, company_id, category_code, version, name, "
                "allowed_evidence_types, attribution_rules, effective_from, effective_to, "
                "status, created_by, published_by, published_at"
                ") VALUES ("
                ":version_id, :company_id, 'MIGRATE', 1, 'Migrate', '[]', '{}', "
                "'2026-01-01', '2026-12-31', 'published', :created_by, :published_by, now()"
                ")"
            ),
            {
                "version_id": version_1_id,
                "company_id": company_id,
                "created_by": user_subject,
                "published_by": user_subject,
            },
        )

        version_2_id = uuid4()
        with pytest.raises(DBAPIError):
            await connection.execute(
                text(
                    "INSERT INTO expense_categories ("
                    "expense_category_version_id, company_id, category_code, version, name, "
                    "allowed_evidence_types, attribution_rules, effective_from, effective_to, "
                    "status, created_by, published_by, published_at"
                    ") VALUES ("
                    ":version_id, :company_id, 'MIGRATE', 2, 'Migrate 2', '[]', '{}', "
                    "'2026-06-01', '2026-09-30', 'published', :created_by, :published_by, now()"
                    ")"
                ),
                {
                    "version_id": version_2_id,
                    "company_id": company_id,
                    "created_by": user_subject,
                    "published_by": user_subject,
                },
            )

    await engine.dispose()


@pytest.mark.asyncio
async def test_expense_migration_downgrade_and_reupgrade_is_safe(postgres_url: str) -> None:
    config = Config(_ALEMBIC_INI)
    config.set_main_option("sqlalchemy.url", postgres_url)

    await asyncio.to_thread(command.downgrade, config, "base")
    await asyncio.to_thread(command.upgrade, config, "head")

    engine = create_async_engine(postgres_url)
    async with engine.begin() as connection:
        assert await _has_table(connection, "expense_categories")
        assert await _has_table(connection, "expense_policies")
        assert await _has_function(connection, "protect_published_expense_categories")
        assert await _has_function(connection, "prevent_overlapping_expense_policies")
    await engine.dispose()
