from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from tradeflow_api.demo_reset import DEMO_LOCK_ID, maintenance_state, require_safe_demo_database
from tradeflow_api.models import metadata


async def reset_demo() -> None:
    database_url = os.environ["TRADEFLOW_DATABASE_URL"]
    database_name = os.environ["TRADEFLOW_DEMO_DATABASE_NAME"]
    require_safe_demo_database(
        database_url, os.environ.get("TRADEFLOW_ENVIRONMENT", ""), database_name
    )
    state_path = Path(os.environ.get("TRADEFLOW_DEMO_STATE_PATH", "/demo-state/status.json"))
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as lock_connection:
            acquired = await lock_connection.scalar(
                text("SELECT pg_try_advisory_lock(:lock_id)"), {"lock_id": DEMO_LOCK_ID}
            )
            if not acquired:
                raise RuntimeError("Another demo reset is already running.")
            try:
                with maintenance_state(state_path):
                    migration = await asyncio.create_subprocess_exec(
                        sys.executable,
                        "-m",
                        "alembic",
                        "-c",
                        "apps/api/alembic.ini",
                        "upgrade",
                        "head",
                    )
                    if await migration.wait() != 0:
                        raise RuntimeError("Demo migration failed.")
                    async with engine.begin() as connection:
                        table_names = ", ".join(
                            f'"{table.name}"' for table in metadata.sorted_tables
                        )
                        await connection.execute(text(f"TRUNCATE TABLE {table_names} CASCADE"))
                    seed = await asyncio.create_subprocess_exec(
                        sys.executable, "scripts/seed_demo.py"
                    )
                    if await seed.wait() != 0:
                        raise RuntimeError("Demo seed failed.")
            finally:
                await lock_connection.execute(
                    text("SELECT pg_advisory_unlock(:lock_id)"), {"lock_id": DEMO_LOCK_ID}
                )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(reset_demo())
