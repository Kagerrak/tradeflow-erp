from __future__ import annotations

import os

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import create_async_engine
from tradeflow_api.models import metadata


@pytest.fixture(scope="session")
def postgres_url() -> str:
    value = os.environ.get(
        "TRADEFLOW_TEST_DATABASE_URL",
        "postgresql+asyncpg://tradeflow:tradeflow@localhost:5433/tradeflow_test",
    )
    return value


@pytest.fixture(scope="session", autouse=True)
def migrated_database(postgres_url: str) -> None:
    config = Config("apps/api/alembic.ini")
    config.set_main_option("sqlalchemy.url", postgres_url)
    command.upgrade(config, "head")


@pytest.fixture(autouse=True)
async def clean_database(
    migrated_database: None,
    postgres_url: str,
) -> None:
    del migrated_database
    engine = create_async_engine(postgres_url)
    async with engine.begin() as connection:
        for table in reversed(metadata.sorted_tables):
            await connection.execute(delete(table))
    await engine.dispose()
