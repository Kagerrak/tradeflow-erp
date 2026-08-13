from __future__ import annotations

import asyncio

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


@pytest.mark.asyncio
async def test_return_authorization_migration_upgrade_downgrade_reupgrade(
    postgres_url: str,
) -> None:
    config = Config("apps/api/alembic.ini")
    config.set_main_option("sqlalchemy.url", postgres_url)
    try:
        await asyncio.to_thread(command.downgrade, config, "d524a29c32b8")
        engine = create_async_engine(postgres_url)
        async with engine.connect() as connection:
            assert await connection.scalar(text("SELECT to_regclass('return_requests')")) is None
        await engine.dispose()

        await asyncio.to_thread(command.upgrade, config, "head")
        engine = create_async_engine(postgres_url)
        async with engine.connect() as connection:
            state = (
                (
                    await connection.execute(
                        text(
                            """
                            SELECT (SELECT version_num FROM alembic_version) AS version_num,
                                   to_regclass('return_requests') IS NOT NULL AS has_requests,
                                   to_regclass('return_authorizations') IS NOT NULL
                                     AS has_authorizations,
                                   to_regprocedure('validate_return_authorization()') IS NOT NULL
                                     AS has_authorization_guard,
                                   currency_minor_scale('JPY') AS jpy_scale,
                                   currency_minor_scale('KWD') AS kwd_scale,
                                   currency_minor_scale('CLF') AS clf_scale
                            """
                        )
                    )
                )
                .mappings()
                .one()
            )
        await engine.dispose()
        assert dict(state) == {
            "version_num": "e93736a741bd",
            "has_requests": True,
            "has_authorizations": True,
            "has_authorization_guard": True,
            "jpy_scale": 0,
            "kwd_scale": 3,
            "clf_scale": 4,
        }
    finally:
        await asyncio.to_thread(command.upgrade, config, "head")
