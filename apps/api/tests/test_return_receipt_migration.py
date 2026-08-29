from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


@pytest.mark.asyncio
async def test_return_receipt_migration_upgrade_downgrade_reupgrade(
    postgres_url: str,
) -> None:
    config = Config("apps/api/alembic.ini")
    config.set_main_option("sqlalchemy.url", postgres_url)
    try:
        await asyncio.to_thread(command.downgrade, config, "e93736a741bd")
        engine = create_async_engine(postgres_url)
        async with engine.begin() as connection:
            assert (await connection.scalar(text("SELECT to_regclass('return_receipts')"))) is None
            assert (
                await connection.scalar(text("SELECT to_regclass('return_request_evidence')"))
            ) is None
            await connection.execute(
                text(
                    """
                    INSERT INTO role_templates(role_template_id, code, name)
                    VALUES (:id, 'WAREHOUSE_SUPERVISOR', 'Warehouse Supervisor')
                    ON CONFLICT (code) DO NOTHING
                    """
                ),
                {"id": uuid4()},
            )
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
                              to_regclass('return_receipts') IS NOT NULL AS has_receipts,
                              to_regclass('return_receipt_lines') IS NOT NULL
                                AS has_receipt_lines,
                              to_regclass('return_receipt_evidence') IS NOT NULL
                                AS has_receipt_evidence,
                              to_regclass('return_request_evidence') IS NOT NULL
                                AS has_request_evidence,
                              to_regprocedure('reject_return_receipt_mutation()')
                                IS NOT NULL AS has_receipt_guard,
                              to_regprocedure('reject_return_receipt_line_mutation()')
                                IS NOT NULL AS has_line_guard,
                              (SELECT count(*) FROM capabilities
                                WHERE code = 'returns:receive') AS has_receive_capability,
                              (SELECT count(*) FROM role_template_capabilities
                                JOIN role_templates USING (role_template_id)
                                WHERE role_templates.code = 'WAREHOUSE_SUPERVISOR'
                                  AND capability_code = 'returns:receive')
                                AS supervisor_has_receive
                            """
                        )
                    )
                )
                .mappings()
                .one()
            )

            type_constraint = await connection.execute(
                text(
                    """
                    SELECT pg_get_constraintdef(oid) AS definition
                    FROM pg_constraint
                    WHERE conname = 'ck_stock_movements_type'
                    """
                )
            )
            type_row = type_constraint.mappings().one()
            assert "authorized_return_receipt" in type_row["definition"]

            leg_constraint = await connection.execute(
                text(
                    """
                    SELECT pg_get_constraintdef(oid) AS definition
                    FROM pg_constraint
                    WHERE conname = 'ck_stock_movements_leg'
                    """
                )
            )
            leg_row = leg_constraint.mappings().one()
            assert "authorized_return_available_in" in leg_row["definition"]
            assert "authorized_return_quarantine_in" in leg_row["definition"]

            triggers = await connection.execute(
                text(
                    """
                    SELECT count(*) AS immutability_triggers
                    FROM pg_trigger
                    WHERE tgname IN (
                      'reject_return_receipt_mutation',
                      'reject_return_receipt_line_mutation',
                      'reject_return_receipt_evidence_mutation'
                    )
                    """
                )
            )
            trigger_row = triggers.mappings().one()
            assert trigger_row["immutability_triggers"] == 3

        await engine.dispose()

        assert dict(state) == {
            "version_num": "0024",
            "has_receipts": True,
            "has_receipt_lines": True,
            "has_receipt_evidence": True,
            "has_request_evidence": True,
            "has_receipt_guard": True,
            "has_line_guard": True,
            "has_receive_capability": 1,
            "supervisor_has_receive": 1,
        }
    finally:
        await asyncio.to_thread(command.upgrade, config, "head")
