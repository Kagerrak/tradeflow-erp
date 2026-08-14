from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from test_credit_note_contract import (
    _posted_invoice,
    _request_credit_note,
)
from test_delivery_confirmation_contract import FakeObjectStorage
from tradeflow_api.app import create_app
from tradeflow_api.config import Settings


@pytest.fixture
def fake_storage() -> FakeObjectStorage:
    return FakeObjectStorage()


@pytest.fixture
def migration_database_url(postgres_url: str) -> str:
    base = postgres_url.rsplit("/", 1)[0]
    return f"{base}/tradeflow_migration_test"


@pytest.fixture
def migration_alembic_config(migration_database_url: str) -> Config:
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", migration_database_url)
    return config


@pytest.fixture
def migration_settings(migration_database_url: str) -> Settings:
    return Settings(
        environment="testing",
        database_url=migration_database_url,
        auth_issuer="https://identity.test",
        auth_audience="tradeflow-api",
        auth_test_secret="test-secret-with-at-least-32-characters",
        telemetry_enabled=False,
    )


@pytest.fixture
async def migration_client(
    migration_settings: Settings,
    fake_storage: FakeObjectStorage,
) -> AsyncIterator[AsyncClient]:
    app = create_app(migration_settings)
    app.state.object_storage = fake_storage
    async with app.router.lifespan_context(app):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
        ) as client:
            yield client


async def _reset_migration_database(admin_url: str, target_url: str) -> None:
    engine = create_async_engine(admin_url)
    async with engine.connect() as connection:
        await connection.execution_options(isolation_level="AUTOCOMMIT")
        await connection.execute(
            text(
                "SELECT pg_terminate_backend(pid) "
                "FROM pg_stat_activity "
                "WHERE datname = 'tradeflow_migration_test' AND pid <> pg_backend_pid()"
            )
        )
        await connection.execute(text("DROP DATABASE IF EXISTS tradeflow_migration_test"))
        await connection.execute(text("CREATE DATABASE tradeflow_migration_test"))
    await engine.dispose()


@pytest.fixture(autouse=True)
def fresh_migration_database(
    migration_database_url: str,
    migration_alembic_config: Config,
) -> None:
    admin_url = migration_database_url.rsplit("/", 1)[0] + "/postgres"
    asyncio.run(_reset_migration_database(admin_url, migration_database_url))
    command.upgrade(migration_alembic_config, "head")


@pytest.mark.asyncio
async def test_credit_note_migration_round_trip(
    migration_client: AsyncClient,
    migration_settings: Settings,
    migration_database_url: str,
    migration_alembic_config: Config,
    fake_storage: FakeObjectStorage,
) -> None:
    _, draft_invoice_id = await _posted_invoice(
        migration_client, migration_settings, migration_database_url, fake_storage
    )
    requested = await _request_credit_note(
        migration_client,
        migration_settings,
        draft_invoice_id,
        amount="10.00",
        idempotency_key="migration-request",
    )
    assert requested["status"] == "pending_authorization"

    # Downgrade is blocked while immutable history exists.
    with pytest.raises(Exception):  # noqa: B017
        await asyncio.to_thread(command.downgrade, migration_alembic_config, "d53dcaa7ede3")

    engine = create_async_engine(migration_database_url)
    async with engine.begin() as connection:
        await connection.execute(text("TRUNCATE TABLE credit_notes CASCADE"))
    await engine.dispose()

    # Empty downgrade to the credit-note merge baseline and re-upgrade succeeds.
    await asyncio.to_thread(command.downgrade, migration_alembic_config, "d53dcaa7ede3")
    await asyncio.to_thread(command.upgrade, migration_alembic_config, "head")

    # Sanity check: the new schema is usable again.
    async with engine.connect() as connection:
        count = await connection.scalar(
            text("SELECT COUNT(*) FROM information_schema.tables WHERE table_name = 'credit_notes'")
        )
    await engine.dispose()
    assert count == 1


@pytest.mark.asyncio
async def test_credit_note_migration_schema_includes_expected_objects(
    migration_database_url: str,
) -> None:
    engine = create_async_engine(migration_database_url)
    async with engine.connect() as connection:
        tables = {
            row["table_name"]
            for row in (
                await connection.execute(
                    text(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'public'"
                    )
                )
            ).mappings()
        }
        columns = {
            row["column_name"]
            for row in (
                await connection.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name = 'document_series_number_audit'"
                    )
                )
            ).mappings()
        }
        triggers = {
            row["trigger_name"]
            for row in (
                await connection.execute(
                    text(
                        "SELECT trigger_name FROM information_schema.triggers "
                        "WHERE event_object_table IN ('credit_notes', 'credit_note_authorizations')"
                    )
                )
            ).mappings()
        }
    await engine.dispose()

    assert "credit_notes" in tables
    assert "credit_note_authorizations" in tables
    assert "credit_note_id" in columns
    assert "trg_credit_notes_immutable" in triggers
    assert "trg_credit_note_authorizations_immutable" in triggers
    assert "trg_credit_note_authorization_valid" in triggers
