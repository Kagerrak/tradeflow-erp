from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from typing import Annotated, Any, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from tradeflow_api.auth import (
    AuthorizedUser,
    require_credit_note_approver,
    require_credit_note_reader,
    require_credit_note_requester,
)
from tradeflow_api.command_receipts import get_command_replay, store_command_result
from tradeflow_api.database import get_database_session
from tradeflow_api.errors import AppError, error_responses
from tradeflow_api.invoice_posting import (
    _base_currency,
    _invoice_ledger_facts,
    _load_invoice,
    _require_branch_scope,
    _update_credit_exposure,
)
from tradeflow_api.models import (
    approval_authorities,
    credit_note_authorizations,
    credit_notes,
    customer_ledger_entries,
    document_series,
    document_series_number_audit,
)

router = APIRouter(prefix="/v1/finance", tags=["finance"])
ZERO = Decimal("0")


class CommandModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RequestCreditNoteCommand(CommandModel):
    amount: Decimal = Field(gt=0, max_digits=24, decimal_places=6)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    reason: str = Field(min_length=1, max_length=500)


class ReverseCreditNoteCommand(CommandModel):
    reason: str = Field(min_length=1, max_length=500)


class CreditNoteResponse(BaseModel):
    credit_note_id: UUID
    draft_invoice_id: UUID
    customer_id: UUID
    branch_id: UUID
    status: str
    amount: Decimal
    currency: str
    reason: str
    requested_by: str
    requested_at: datetime
    number: str | None
    posted_by: str | None
    posted_at: datetime | None
    ledger_entry_id: UUID | None
    reversed_by: str | None
    reversed_at: datetime | None
    reversal_reason: str | None
    reversal_ledger_entry_id: UUID | None


class CreditNotePostedResponse(BaseModel):
    credit_note_id: UUID
    draft_invoice_id: UUID
    status: str
    number: str
    ledger_entry_id: UUID
    posted_by: str
    posted_at: datetime


class CreditNoteReversedResponse(BaseModel):
    credit_note_id: UUID
    status: str
    reversed_by: str
    reversed_at: datetime
    reversal_ledger_entry_id: UUID


class CreditNoteListResponse(BaseModel):
    items: list[CreditNoteResponse]
    total: int


def _hash_request(resource_id: UUID, body: dict[str, Any]) -> str:
    payload = {"resource_id": str(resource_id), **body}
    return sha256(
        "μ".join(f"{key}={value}" for key, value in sorted(payload.items())).encode()
    ).hexdigest()


def _hash_request_no_body(resource_id: UUID) -> str:
    return sha256(f"resource_id={resource_id}".encode()).hexdigest()


async def _lock(session: AsyncSession, key: str) -> None:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": key},
    )


async def _load_credit_note(session: AsyncSession, credit_note_id: UUID) -> dict[str, Any]:
    row = (
        (
            await session.execute(
                select(credit_notes).where(credit_notes.c.credit_note_id == credit_note_id)
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise AppError(404, "credit_note_not_found", "Credit Note not found.")
    return dict(row)


async def _invoice_open_balance(session: AsyncSession, draft_invoice_id: UUID) -> Decimal:
    total = await session.scalar(
        select(func.coalesce(func.sum(customer_ledger_entries.c.amount), ZERO)).where(
            customer_ledger_entries.c.invoice_id == draft_invoice_id
        )
    )
    return cast(Decimal, total) if total is not None else ZERO


async def _pending_credit_notes_total(
    session: AsyncSession, draft_invoice_id: UUID, exclude_credit_note_id: UUID | None = None
) -> Decimal:
    query = select(func.coalesce(func.sum(credit_notes.c.amount), ZERO)).where(
        credit_notes.c.draft_invoice_id == draft_invoice_id,
        credit_notes.c.status == "pending_authorization",
    )
    if exclude_credit_note_id is not None:
        query = query.where(credit_notes.c.credit_note_id != exclude_credit_note_id)
    total = await session.scalar(query)
    return cast(Decimal, total) if total is not None else ZERO


async def _invoice_eligible_credit(
    session: AsyncSession, draft_invoice_id: UUID, exclude_credit_note_id: UUID | None = None
) -> Decimal:
    open_balance = await _invoice_open_balance(session, draft_invoice_id)
    pending = await _pending_credit_notes_total(session, draft_invoice_id, exclude_credit_note_id)
    return max(open_balance - pending, ZERO)


async def _require_credit_note_authority(
    session: AsyncSession,
    actor: AuthorizedUser,
    branch_id: UUID,
    amount: Decimal,
    maker_subject: str,
) -> UUID:
    if actor.subject == maker_subject:
        raise AppError(
            status_code=403,
            code="credit_note_maker_checker_required",
            message="Requester cannot authorize the same Credit Note.",
        )
    row = (
        (
            await session.execute(
                select(approval_authorities).where(
                    approval_authorities.c.user_subject == actor.subject,
                    approval_authorities.c.capability_code == "finance:credit-note-approve",
                    approval_authorities.c.branch_id == branch_id,
                )
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise AppError(
            status_code=403,
            code="approval_authority_required",
            message="You do not have Credit Note approval authority for this branch.",
        )
    maximum_amount = row["maximum_amount"]
    if maximum_amount is not None and amount > maximum_amount:
        raise AppError(
            status_code=403,
            code="approval_limit_exceeded",
            message="Credit Note amount exceeds your approval limit.",
        )
    return cast(UUID, row["approval_authority_id"])


async def _issue_credit_note_number(
    session: AsyncSession, branch_id: UUID
) -> tuple[UUID, int, str]:
    series = (
        (
            await session.execute(
                select(document_series)
                .where(
                    document_series.c.branch_id == branch_id,
                    document_series.c.document_type == "credit_note",
                )
                .with_for_update()
            )
        )
        .mappings()
        .one_or_none()
    )
    if series is None:
        raise AppError(
            status_code=503,
            code="credit_note_series_not_configured",
            message="Credit Note document series is not configured for this branch.",
        )
    series_id = cast(UUID, series["document_series_id"])
    series_number = cast(int, series["next_number"])
    prefix = cast(str, series["prefix"])
    await session.execute(
        update(document_series)
        .where(document_series.c.document_series_id == series_id)
        .values(next_number=document_series.c.next_number + 1)
    )
    number = f"{prefix}-{series_number:08d}"
    return series_id, series_number, number


@router.post(
    "/invoices/{draft_invoice_id}/credit-notes",
    response_model=CreditNoteResponse,
    responses=error_responses(400, 401, 403, 404, 409, 422, 503),
    status_code=201,
)
async def request_credit_note(
    draft_invoice_id: UUID,
    command: RequestCreditNoteCommand,
    request: Request,
    response: Response,
    actor: Annotated[AuthorizedUser, Depends(require_credit_note_requester)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=200),
    ] = None,
) -> CreditNoteResponse:
    if idempotency_key is None:
        raise AppError(
            status_code=400,
            code="idempotency_key_required",
            message="Idempotency-Key is required for this command.",
        )

    request_hash = _hash_request(
        draft_invoice_id,
        {
            "amount": str(command.amount),
            "currency": command.currency,
            "reason": command.reason,
        },
    )
    await session.rollback()
    async with session.begin():
        replay = await get_command_replay(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            response.status_code = 200
            response.headers["X-Idempotency-Replayed"] = "true"
            return CreditNoteResponse.model_validate(replay)

        await _lock(session, f"credit-note-request:invoice:{draft_invoice_id}")
        invoice = await _load_invoice(session, draft_invoice_id)
        await _require_branch_scope(actor, invoice["branch_id"])

        base_currency = await _base_currency(session)
        if invoice["currency"] != base_currency or command.currency != base_currency:
            raise AppError(
                status_code=422,
                code="unsupported_invoice_currency",
                message="Credit notes must use the company base currency.",
            )

        status, _ = await _invoice_ledger_facts(session, draft_invoice_id)
        if status != "posted":
            raise AppError(
                status_code=409,
                code="invoice_not_creditable",
                message="Credit notes can only be requested against posted invoices.",
            )

        eligible = await _invoice_eligible_credit(session, draft_invoice_id)
        if command.amount > eligible:
            raise AppError(
                status_code=409,
                code="credit_note_exceeds_eligible_value",
                message="Credit Note amount exceeds the invoice's eligible credit value.",
            )

        credit_note_id = uuid4()
        await session.execute(
            insert(credit_notes).values(
                credit_note_id=credit_note_id,
                draft_invoice_id=draft_invoice_id,
                customer_id=invoice["customer_id"],
                branch_id=invoice["branch_id"],
                amount=command.amount,
                currency=command.currency,
                reason=command.reason,
                requested_by=actor.subject,
                status="pending_authorization",
                correlation_id=request.state.correlation_id,
                idempotency_key=idempotency_key,
            )
        )
        result = CreditNoteResponse(
            credit_note_id=credit_note_id,
            draft_invoice_id=draft_invoice_id,
            customer_id=invoice["customer_id"],
            branch_id=invoice["branch_id"],
            status="pending_authorization",
            amount=command.amount,
            currency=command.currency,
            reason=command.reason,
            requested_by=actor.subject,
            requested_at=datetime.now(UTC),
            number=None,
            posted_by=None,
            posted_at=None,
            ledger_entry_id=None,
            reversed_by=None,
            reversed_at=None,
            reversal_reason=None,
            reversal_ledger_entry_id=None,
        )
        await store_command_result(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            result=result,
        )
        response.headers["X-Idempotency-Replayed"] = "false"
        return result


@router.post(
    "/credit-notes/{credit_note_id}/post",
    response_model=CreditNotePostedResponse,
    responses=error_responses(400, 401, 403, 404, 409, 503),
    status_code=201,
)
async def authorize_and_post_credit_note(
    credit_note_id: UUID,
    command: CommandModel,
    request: Request,
    response: Response,
    actor: Annotated[AuthorizedUser, Depends(require_credit_note_approver)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=200),
    ] = None,
) -> CreditNotePostedResponse:
    if idempotency_key is None:
        raise AppError(
            status_code=400,
            code="idempotency_key_required",
            message="Idempotency-Key is required for this command.",
        )

    request_hash = _hash_request_no_body(credit_note_id)
    await session.rollback()
    async with session.begin():
        replay = await get_command_replay(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            response.status_code = 200
            response.headers["X-Idempotency-Replayed"] = "true"
            return CreditNotePostedResponse.model_validate(replay)

        await _lock(session, f"credit-note:{credit_note_id}")
        note = await _load_credit_note(session, credit_note_id)
        await _require_branch_scope(actor, note["branch_id"])

        if note["status"] != "pending_authorization":
            raise AppError(
                status_code=409,
                code="credit_note_not_authorizable",
                message="Credit Note is not pending authorization.",
            )

        eligible = await _invoice_eligible_credit(
            session, note["draft_invoice_id"], exclude_credit_note_id=credit_note_id
        )
        if note["amount"] > eligible:
            raise AppError(
                status_code=409,
                code="credit_note_exceeds_eligible_value",
                message="Credit Note amount exceeds the invoice's eligible credit value.",
            )

        authority_id = await _require_credit_note_authority(
            session,
            actor,
            note["branch_id"],
            note["amount"],
            cast(str, note["requested_by"]),
        )

        series_id, series_number, number = await _issue_credit_note_number(
            session, note["branch_id"]
        )

        posted_at = datetime.now(UTC)
        ledger_entry_id = uuid4()
        await session.execute(
            insert(customer_ledger_entries).values(
                entry_id=ledger_entry_id,
                customer_id=note["customer_id"],
                entry_type="credit_note",
                source_type="credit_note",
                source_id=credit_note_id,
                invoice_id=note["draft_invoice_id"],
                amount=-note["amount"],
                currency=note["currency"],
                branch_id=note["branch_id"],
                actor_subject=actor.subject,
                correlation_id=request.state.correlation_id,
                idempotency_key=f"{idempotency_key}:ledger",
                posted_at=posted_at,
            )
        )
        await _update_credit_exposure(
            session,
            customer_id=note["customer_id"],
            commercial_approval_id=None,
            sales_order_id=None,
            open_balance_delta=-note["amount"],
            approved_uninvoiced_delta=ZERO,
            source_type="credit_note",
            source_id=credit_note_id,
            actor_subject=actor.subject,
            correlation_id=request.state.correlation_id,
            idempotency_key=f"{idempotency_key}:exposure",
        )
        await session.execute(
            insert(document_series_number_audit).values(
                document_series_number_audit_id=uuid4(),
                document_series_id=series_id,
                series_number=series_number,
                status="issued",
                credit_note_id=credit_note_id,
            )
        )
        await session.execute(
            insert(credit_note_authorizations).values(
                credit_note_id=credit_note_id,
                authorized_by=actor.subject,
                approval_authority_id=authority_id,
                idempotency_key=idempotency_key,
                correlation_id=request.state.correlation_id,
            )
        )
        await session.execute(
            update(credit_notes)
            .where(credit_notes.c.credit_note_id == credit_note_id)
            .values(
                status="posted",
                document_series_id=series_id,
                series_number=series_number,
                number=number,
                posted_by=actor.subject,
                posted_at=posted_at,
                ledger_entry_id=ledger_entry_id,
            )
        )
        result = CreditNotePostedResponse(
            credit_note_id=credit_note_id,
            draft_invoice_id=note["draft_invoice_id"],
            status="posted",
            number=number,
            ledger_entry_id=ledger_entry_id,
            posted_by=actor.subject,
            posted_at=posted_at,
        )
        await store_command_result(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            result=result,
        )
        response.headers["X-Idempotency-Replayed"] = "false"
        return result


@router.post(
    "/credit-notes/{credit_note_id}/reverse",
    response_model=CreditNoteReversedResponse,
    responses=error_responses(400, 401, 403, 404, 409, 503),
    status_code=201,
)
async def reverse_credit_note(
    credit_note_id: UUID,
    command: ReverseCreditNoteCommand,
    request: Request,
    response: Response,
    actor: Annotated[AuthorizedUser, Depends(require_credit_note_approver)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=200),
    ] = None,
) -> CreditNoteReversedResponse:
    if idempotency_key is None:
        raise AppError(
            status_code=400,
            code="idempotency_key_required",
            message="Idempotency-Key is required for this command.",
        )

    request_hash = _hash_request(
        credit_note_id,
        {"reason": command.reason},
    )
    await session.rollback()
    async with session.begin():
        replay = await get_command_replay(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
        )
        if replay is not None:
            response.status_code = 200
            response.headers["X-Idempotency-Replayed"] = "true"
            return CreditNoteReversedResponse.model_validate(replay)

        await _lock(session, f"credit-note:{credit_note_id}")
        note = await _load_credit_note(session, credit_note_id)
        await _require_branch_scope(actor, note["branch_id"])

        if note["status"] != "posted":
            raise AppError(
                status_code=409,
                code="credit_note_not_reversible",
                message="Only posted Credit Notes can be reversed.",
            )
        if actor.subject == note["requested_by"]:
            raise AppError(
                status_code=403,
                code="credit_note_maker_checker_required",
                message="Requester cannot reverse the same Credit Note.",
            )

        await _require_credit_note_authority(
            session,
            actor,
            note["branch_id"],
            note["amount"],
            cast(str, note["requested_by"]),
        )

        reversed_at = datetime.now(UTC)
        reversal_ledger_entry_id = uuid4()
        await session.execute(
            insert(customer_ledger_entries).values(
                entry_id=reversal_ledger_entry_id,
                customer_id=note["customer_id"],
                entry_type="credit_note",
                source_type="credit_note_reversal",
                source_id=credit_note_id,
                invoice_id=note["draft_invoice_id"],
                amount=note["amount"],
                currency=note["currency"],
                branch_id=note["branch_id"],
                actor_subject=actor.subject,
                correlation_id=request.state.correlation_id,
                idempotency_key=f"{idempotency_key}:ledger",
                posted_at=reversed_at,
            )
        )
        await _update_credit_exposure(
            session,
            customer_id=note["customer_id"],
            commercial_approval_id=None,
            sales_order_id=None,
            open_balance_delta=note["amount"],
            approved_uninvoiced_delta=ZERO,
            source_type="credit_note_reversal",
            source_id=credit_note_id,
            actor_subject=actor.subject,
            correlation_id=request.state.correlation_id,
            idempotency_key=f"{idempotency_key}:exposure",
        )
        await session.execute(
            update(credit_notes)
            .where(credit_notes.c.credit_note_id == credit_note_id)
            .values(
                status="reversed",
                reversed_by=actor.subject,
                reversed_at=reversed_at,
                reversal_reason=command.reason,
                reversal_ledger_entry_id=reversal_ledger_entry_id,
            )
        )
        result = CreditNoteReversedResponse(
            credit_note_id=credit_note_id,
            status="reversed",
            reversed_by=actor.subject,
            reversed_at=reversed_at,
            reversal_ledger_entry_id=reversal_ledger_entry_id,
        )
        await store_command_result(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            result=result,
        )
        response.headers["X-Idempotency-Replayed"] = "false"
        return result


@router.get(
    "/credit-notes/{credit_note_id}",
    response_model=CreditNoteResponse,
    responses=error_responses(401, 403, 404, 503),
)
async def get_credit_note(
    credit_note_id: UUID,
    actor: Annotated[AuthorizedUser, Depends(require_credit_note_reader)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> CreditNoteResponse:
    note = await _load_credit_note(session, credit_note_id)
    await _require_branch_scope(actor, note["branch_id"])
    return CreditNoteResponse.model_validate(note)


@router.get(
    "/credit-notes",
    response_model=CreditNoteListResponse,
    responses=error_responses(401, 403, 503),
)
async def list_credit_notes(
    actor: Annotated[AuthorizedUser, Depends(require_credit_note_reader)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    draft_invoice_id: Annotated[UUID | None, Query()] = None,
    status: Annotated[str | None, Query()] = None,
    customer_id: Annotated[UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CreditNoteListResponse:
    query = select(credit_notes).where(credit_notes.c.branch_id.in_(actor.branch_ids))
    if draft_invoice_id is not None:
        query = query.where(credit_notes.c.draft_invoice_id == draft_invoice_id)
    if status is not None:
        query = query.where(credit_notes.c.status == status)
    if customer_id is not None:
        query = query.where(credit_notes.c.customer_id == customer_id)

    total = await session.scalar(select(func.count()).select_from(query.subquery()))
    rows = (
        (
            await session.execute(
                query.order_by(credit_notes.c.requested_at.desc()).limit(limit).offset(offset)
            )
        )
        .mappings()
        .all()
    )
    return CreditNoteListResponse(
        items=[CreditNoteResponse.model_validate(dict(row)) for row in rows],
        total=total or 0,
    )
