from __future__ import annotations

import asyncio

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


@pytest.mark.asyncio
async def test_return_evidence_migration_upgrade_downgrade_reupgrade(
    postgres_url: str,
) -> None:
    config = Config("apps/api/alembic.ini")
    config.set_main_option("sqlalchemy.url", postgres_url)
    try:
        await asyncio.to_thread(command.downgrade, config, "e93736a741bd")
        engine = create_async_engine(postgres_url)
        async with engine.connect() as connection:
            assert (
                await connection.scalar(text("SELECT to_regclass('return_request_evidence')"))
            ) is None
        await engine.dispose()

        await asyncio.to_thread(command.upgrade, config, "head")
        engine = create_async_engine(postgres_url)
        async with engine.connect() as connection:
            state = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT
                              (SELECT version_num FROM alembic_version) AS version_num,
                              to_regclass('return_request_evidence') IS NOT NULL AS has_evidence,
                              to_regclass('return_request_evidence_sync_state') IS NOT NULL
                                AS has_sync_state,
                              EXISTS (
                                SELECT 1 FROM capabilities
                                WHERE code = 'returns:evidence-capture'
                              ) AS has_capture_capability,
                              EXISTS (
                                SELECT 1 FROM capabilities
                                WHERE code = 'returns:evidence-read'
                              ) AS has_read_capability
                            """
                        )
                    )
                )
                .mappings()
                .one()
            )
        await engine.dispose()
        assert dict(state) == {
            "version_num": "5db106ff1092",
            "has_evidence": True,
            "has_sync_state": True,
            "has_capture_capability": True,
            "has_read_capability": True,
        }
    finally:
        await asyncio.to_thread(command.upgrade, config, "head")
