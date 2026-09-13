from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from fastapi import Request
from opentelemetry import trace
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool


class IamAuthTokenProvider:
    """Supplies a fresh RDS IAM authentication token for every connection.

    The demo cluster is an Aurora express-configuration cluster, which supports
    IAM authentication only and is reached over the internet access gateway, so
    there is no static password to store. asyncpg accepts a callable for the
    password and calls it once per new connection, which is exactly the
    lifetime of an RDS auth token (15 minutes).
    """

    def __init__(self, database_url: str, region: str | None) -> None:
        url = make_url(database_url)
        if url.host is None or url.username is None:
            raise ValueError("A database host and user are required for IAM authentication.")
        self._host = url.host
        self._port = url.port or 5432
        self._user = url.username
        self._region = region
        import boto3  # type: ignore[import-untyped]

        self._client = boto3.client("rds", region_name=region)

    def __call__(self) -> str:
        return str(
            self._client.generate_db_auth_token(
                DBHostname=self._host,
                Port=self._port,
                DBUsername=self._user,
                Region=self._region,
            )
        )


def create_database_engine(
    database_url: str,
    *,
    lambda_runtime: bool = False,
    iam_auth: bool = False,
    region: str | None = None,
) -> AsyncEngine:
    """Build the async engine for this process.

    In Lambda the engine must not hold idle connections between invocations:
    Aurora Serverless v2 can only scale to zero when nothing is connected, and a
    cached execution environment would otherwise keep a session open for
    minutes after the last request.  ``NullPool`` gives every checkout a fresh
    connection and closes it on release, so a Lambda that is no longer serving
    traffic leaves nothing behind.
    """
    connect_args: dict[str, object] = {}
    if iam_auth:
        # IAM authentication requires TLS, and the token replaces the password.
        connect_args = {
            "password": IamAuthTokenProvider(database_url, region),
            "ssl": "require",
        }
    if lambda_runtime:
        return create_async_engine(
            database_url,
            poolclass=NullPool,
            connect_args={**connect_args, "timeout": 30, "command_timeout": 60},
        )
    return create_async_engine(database_url, pool_pre_ping=True, connect_args=connect_args)


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
