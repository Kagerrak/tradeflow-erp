from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import Request
from opentelemetry import trace
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)


def create_database_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(database_url, pool_pre_ping=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def check_database(
    engine: AsyncEngine,
    correlation_id: str | None = None,
) -> None:
    with trace.get_tracer(__name__).start_as_current_span("tradeflow.database.check") as span:
        if correlation_id is not None:
            span.set_attribute("tradeflow.correlation_id", correlation_id)
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))


def migration_heads(config_path: Path) -> set[str]:
    config = Config(str(config_path))
    return set(ScriptDirectory.from_config(config).get_heads())


async def check_database_migrations(
    engine: AsyncEngine,
    expected_heads: set[str],
) -> None:
    async with engine.connect() as connection:
        revisions = (
            await connection.execute(text("SELECT version_num FROM alembic_version"))
        ).scalars()
        if set(revisions) != expected_heads:
            raise RuntimeError(
                "PostgreSQL migrations are not current; run `pnpm migrate` before startup."
            )


async def get_database_session(request: Request) -> AsyncIterator[AsyncSession]:
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with factory() as session:
        yield session
