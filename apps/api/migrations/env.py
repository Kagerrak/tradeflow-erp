from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection
from tradeflow_api.database import create_database_engine
from tradeflow_api.models import metadata

config = context.config

if database_url := os.environ.get("TRADEFLOW_DATABASE_URL"):
    config.set_main_option("sqlalchemy.url", database_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def _iam_auth_enabled() -> bool:
    return os.environ.get("TRADEFLOW_DB_IAM_AUTH", "").lower() in {"1", "true", "yes"}


async def run_async_migrations() -> None:
    # Uses the application's engine factory so migrations authenticate the same
    # way the API does. Aurora express clusters have no password: they take a
    # short-lived IAM token over TLS.
    connectable = create_database_engine(
        config.get_main_option("sqlalchemy.url"),
        iam_auth=_iam_auth_enabled(),
        region=os.environ.get("TRADEFLOW_AWS_REGION") or os.environ.get("AWS_REGION"),
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
