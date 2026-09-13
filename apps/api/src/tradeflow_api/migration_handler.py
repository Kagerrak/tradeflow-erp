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
from sqlalchemy.ext.asyncio import create_async_engine


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


def _current_revision(url: str) -> str | None:
    async def read() -> str | None:
        engine = create_async_engine(url, poolclass=None)
        try:
            async with engine.connect() as connection:
                return await connection.run_sync(
                    lambda sync_connection: MigrationContext.configure(
                        sync_connection
                    ).get_current_revision()
                )
        finally:
            await engine.dispose()

    return asyncio.run(read())


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
