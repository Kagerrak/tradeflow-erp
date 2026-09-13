"""Run Alembic migrations as an explicit deployment step.

Schema changes never happen inside application startup or a cold start.  The
deployment workflow invokes this function (inside the VPC, so it can reach
private Aurora) after the new images are published and before traffic moves.

The same function performs the reverse migration used by the rollback
procedure, so both directions are auditable in CloudWatch.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tradeflow_api.database import create_database_engine


def alembic_ini() -> Path:
    configured = os.environ.get("TRADEFLOW_ALEMBIC_INI")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[2] / "alembic.ini"


def _database_url() -> str:
    url = os.environ.get("TRADEFLOW_DATABASE_URL")
    if not url:
        raise RuntimeError("TRADEFLOW_DATABASE_URL is required to run migrations.")
    return url


def _iam_auth() -> bool:
    return os.environ.get("TRADEFLOW_DB_IAM_AUTH", "").lower() in {"1", "true", "yes"}


def engine_for(url: str) -> AsyncEngine:
    return create_database_engine(
        url,
        iam_auth=_iam_auth(),
        region=os.environ.get("TRADEFLOW_AWS_REGION") or os.environ.get("AWS_REGION"),
    )


def _current_revision(url: str) -> str | None:
    async def read() -> str | None:
        engine = engine_for(url)
        try:
            async with engine.connect() as connection:
                revision = await connection.run_sync(
                    lambda sync_connection: MigrationContext.configure(
                        sync_connection
                    ).get_current_revision()
                )
                return str(revision) if revision is not None else None
        finally:
            await engine.dispose()

    return asyncio.run(read())


async def _ensure_database(url: str, name: str) -> None:
    """Create the application database if it does not exist.

    Express-configuration clusters cannot be created with an initial database,
    so this runs as an explicit deployment step. It connects to the maintenance
    database first, and CREATE DATABASE needs autocommit.
    """
    admin_url = url.rsplit("/", 1)[0] + "/postgres"
    engine = create_database_engine(
        admin_url,
        iam_auth=_iam_auth(),
        region=os.environ.get("TRADEFLOW_AWS_REGION") or os.environ.get("AWS_REGION"),
    )
    try:
        async with engine.connect() as connection:
            # Isolation level must be set before the first statement, because
            # CREATE DATABASE cannot run inside a transaction block.
            autocommit = await connection.execution_options(isolation_level="AUTOCOMMIT")
            exists = await autocommit.scalar(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": name}
            )
            if exists:
                return
            await autocommit.execute(text(f'CREATE DATABASE "{name}"'))
    finally:
        await engine.dispose()


def handler(event: dict[str, Any] | None, context: Any = None) -> dict[str, Any]:
    """Apply (default) or revert a migration revision."""
    del context
    request = event or {}
    action = str(request.get("action", "upgrade"))
    url = _database_url()

    config = Config(str(alembic_ini()))
    config.set_main_option("sqlalchemy.url", url)

    try:
        before = _current_revision(url)
    except Exception:
        before = None

    if action == "create-database":
        name = request.get("name")
        if not isinstance(name, str) or not name:
            raise RuntimeError("A database name is required.")
        asyncio.run(_ensure_database(url, name))
        return {"action": action, "database": name}
    if action == "upgrade":
        command.upgrade(config, str(request.get("revision", "head")))
    elif action == "downgrade":
        revision = request.get("revision")
        if not isinstance(revision, str) or not revision:
            raise RuntimeError("A target revision is required to downgrade.")
        command.downgrade(config, revision)
    elif action == "current":
        pass
    else:
        raise RuntimeError(f"Unsupported migration action {action!r}.")

    return {
        "action": action,
        "previous_revision": before,
        "revision": _current_revision(url),
    }
