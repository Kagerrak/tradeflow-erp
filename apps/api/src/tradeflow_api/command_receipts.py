from __future__ import annotations

from uuid import uuid4

from pydantic import BaseModel
from sqlalchemy import insert, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from tradeflow_api.errors import AppError
from tradeflow_api.models import platform_command_receipts


async def get_command_replay(
    session: AsyncSession,
    *,
    actor_subject: str,
    idempotency_key: str,
    request_hash: str,
) -> dict[str, object] | None:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:idempotency_key))"),
        {"idempotency_key": idempotency_key},
    )
    receipt = (
        await session.execute(
            select(
                platform_command_receipts.c.actor_subject,
                platform_command_receipts.c.request_hash,
                platform_command_receipts.c.response_json,
            ).where(platform_command_receipts.c.idempotency_key == idempotency_key)
        )
    ).one_or_none()
    if receipt is None:
        return None
    if receipt.actor_subject != actor_subject or receipt.request_hash != request_hash:
        raise AppError(
            status_code=409,
            code="idempotency_conflict",
            message="Idempotency-Key was already used for another command.",
        )
    return dict(receipt.response_json)


async def store_command_result(
    session: AsyncSession,
    *,
    actor_subject: str,
    idempotency_key: str,
    request_hash: str,
    result: BaseModel,
) -> None:
    await session.execute(
        insert(platform_command_receipts).values(
            command_id=uuid4(),
            idempotency_key=idempotency_key,
            actor_subject=actor_subject,
            request_hash=request_hash,
            response_json=result.model_dump(mode="json"),
        )
    )
