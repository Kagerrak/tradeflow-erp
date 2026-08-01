from __future__ import annotations

import os

from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from tradeflow_api.models import metadata


def require_safe_test_database(database_url: str, environment: str) -> None:
    database_name = make_url(database_url).database
    if environment != "testing" or database_name is None or not database_name.endswith("_test"):
        raise RuntimeError(
            "Refusing to clear a database unless TRADEFLOW_ENVIRONMENT=testing "
            "and its name ends with '_test'."
        )


async def clear_test_database() -> None:
    database_url = os.environ["TRADEFLOW_TEST_DATABASE_URL"]
    environment = os.environ.get("TRADEFLOW_ENVIRONMENT", "")
    require_safe_test_database(database_url, environment)
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            table_names = ", ".join(f'"{table.name}"' for table in metadata.sorted_tables)
            await connection.execute(text(f"TRUNCATE TABLE {table_names} CASCADE"))
    finally:
        await engine.dispose()
