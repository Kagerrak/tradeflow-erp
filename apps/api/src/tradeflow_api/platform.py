from __future__ import annotations

from hashlib import sha256
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from tradeflow_api.auth import CurrentUser, require_platform_writer
from tradeflow_api.database import get_database_session
from tradeflow_api.errors import AppError, error_responses
from tradeflow_api.models import platform_command_receipts

router = APIRouter(prefix="/v1/platform", tags=["platform"])


class PingCommand(BaseModel):
    message: str = Field(min_length=1, max_length=200)


class PingResponse(BaseModel):
    command_id: str
    message: str


@router.post(
    "/ping",
    response_model=PingResponse,
    responses=error_responses(400, 401, 403, 409, 422, 503),
    status_code=201,
)
async def ping(
    command: PingCommand,
    response: Response,
    user: Annotated[CurrentUser, Depends(require_platform_writer)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=200),
    ] = None,
) -> PingResponse:
    if idempotency_key is None:
        raise AppError(
            status_code=400,
            code="idempotency_key_required",
            message="Idempotency-Key is required for this command.",
        )

    request_hash = sha256(command.model_dump_json().encode()).hexdigest()
    command_id = uuid4()
    result_json = {
        "command_id": str(command_id),
        "message": command.message,
    }

    async with session.begin():
        inserted = await session.execute(
            insert(platform_command_receipts)
            .values(
                command_id=command_id,
                idempotency_key=idempotency_key,
                actor_subject=user.subject,
                request_hash=request_hash,
                response_json=result_json,
            )
            .on_conflict_do_nothing(index_elements=["idempotency_key"])
            .returning(platform_command_receipts.c.response_json)
        )
        stored = inserted.scalar_one_or_none()

        replayed = stored is None
        if replayed:
            existing = await session.execute(
                select(
                    platform_command_receipts.c.request_hash,
                    platform_command_receipts.c.response_json,
                ).where(platform_command_receipts.c.idempotency_key == idempotency_key)
            )
            existing_hash, stored = existing.one()
            if existing_hash != request_hash:
                raise AppError(
                    status_code=409,
                    code="idempotency_conflict",
                    message="Idempotency-Key was already used for another command.",
                )

    response.status_code = 200 if replayed else 201
    response.headers["X-Idempotency-Replayed"] = str(replayed).lower()
    return PingResponse.model_validate(stored)
