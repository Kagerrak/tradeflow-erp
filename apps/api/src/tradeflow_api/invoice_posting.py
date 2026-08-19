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
    require_invoice_poster,
    require_invoice_reader,
    require_invoice_voider,
)
from tradeflow_api.command_receipts import get_command_replay, store_command_result
from tradeflow_api.database import get_database_session
from tradeflow_api.errors import AppError, error_responses
from tradeflow_api.models import (
    companies,
    credit_exposure_entries,
    customer_accounts,
    customer_credit_exposure,
    customer_ledger_entries,
    draft_invoice_lines,
    draft_invoices,
)
from tradeflow_api.payment_allocation import auto_allocate_invoice

router = APIRouter(prefix="/v1/finance/invoices", tags=["finance"])
ZERO = Decimal("0")


class CommandModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PostInvoiceCommand(CommandModel):
    posted_at: datetime | None = None


class VoidInvoiceCommand(CommandModel):
    reason: str = Field(min_length=1, max_length=500)


class DraftInvoiceLineResponse(BaseModel):
    draft_invoice_line_id: UUID
    line_id: UUID
    sku_id: UUID
    accepted_quantity_base: Decimal
    unit_price: Decimal
    subtotal: Decimal
    discount_amount: Decimal
    tax_amount: Decimal
    line_total: Decimal


class DraftInvoiceResponse(BaseModel):
    draft_invoice_id: UUID
    delivery_confirmation_id: UUID
    invoice_kind: str
    status: str
    sales_order_id: UUID
    sales_order_revision_id: UUID
    customer_id: UUID
    branch_id: UUID
    currency: str
    subtotal: Decimal
    discount_total: Decimal
    tax_total: Decimal
    grand_total: Decimal
    open_balance: Decimal
    posted_at: datetime | None
    created_at: datetime
    lines: list[DraftInvoiceLineResponse]


class DraftInvoiceListResponse(BaseModel):
    items: list[DraftInvoiceResponse]
    total: int


class PostedInvoiceResponse(BaseModel):
    draft_invoice_id: UUID
    ledger_entry_id: UUID
    status: str
    posted_at: datetime


class InvoiceVoidResponse(BaseModel):
    draft_invoice_id: UUID
    ledger_entry_id: UUID
    status: str


def _hash_request(draft_invoice_id: UUID, body: dict[str, Any]) -> str:
    payload = {"draft_invoice_id": str(draft_invoice_id), **body}
    return sha256(
        "μ".join(f"{key}={value}" for key, value in sorted(payload.items())).encode()
    ).hexdigest()


async def _lock(session: AsyncSession, key: str) -> None:
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
        {"key": key},
    )


async def _require_branch_scope(actor: AuthorizedUser, branch_id: UUID) -> None:
    if branch_id not in actor.branch_ids:
        raise AppError(
            status_code=403,
            code="operational_scope_required",
            message="The requested Branch is outside your operational scope.",
        )


async def _base_currency(session: AsyncSession) -> str:
    currency = await session.scalar(select(companies.c.base_currency))
    if currency is None:
        raise AppError(503, "company_not_configured", "Company is not configured.")
    return cast(str, currency)


async def _available_uninvoiced(session: AsyncSession, customer_id: UUID) -> Decimal:
    exposure = (
        await session.execute(
            select(customer_credit_exposure.c.approved_uninvoiced).where(
                customer_credit_exposure.c.customer_id == customer_id
            )
        )
    ).scalar_one_or_none()
    return cast(Decimal, exposure) if exposure is not None else ZERO


async def _posted_uninvoiced_reduction(session: AsyncSession, draft_invoice_id: UUID) -> Decimal:
    row = (
        await session.execute(
            select(credit_exposure_entries.c.amount_delta).where(
                credit_exposure_entries.c.source_type == "draft_invoice",
                credit_exposure_entries.c.source_id == draft_invoice_id,
                credit_exposure_entries.c.component == "approved_uninvoiced",
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return ZERO
    return -cast(Decimal, row)


async def _invoice_ledger_facts(
    session: AsyncSession, draft_invoice_id: UUID
) -> tuple[str, datetime | None]:
    rows = (
        (
            await session.execute(
                select(
                    customer_ledger_entries.c.entry_type, customer_ledger_entries.c.posted_at
                ).where(customer_ledger_entries.c.invoice_id == draft_invoice_id)
            )
        )
        .mappings()
        .all()
    )
    posted_at: datetime | None = None
    has_invoice = False
    has_void = False
    for row in rows:
        if row["entry_type"] == "invoice":
            has_invoice = True
            posted_at = row["posted_at"]
        elif row["entry_type"] == "void":
            has_void = True
    if has_void:
        return "voided", posted_at
    if has_invoice:
        return "posted", posted_at
    return "draft", None


async def _ledger_status_map(
    session: AsyncSession, invoice_ids: list[UUID]
) -> dict[UUID, tuple[str, datetime | None]]:
    if not invoice_ids:
        return {}
    rows = (
        (
            await session.execute(
                select(
                    customer_ledger_entries.c.invoice_id,
                    customer_ledger_entries.c.entry_type,
                    customer_ledger_entries.c.posted_at,
                ).where(customer_ledger_entries.c.invoice_id.in_(invoice_ids))
            )
        )
        .mappings()
        .all()
    )
    result: dict[UUID, tuple[str, datetime | None]] = {}
    for row in rows:
        invoice_id = row["invoice_id"]
        current_status, _ = result.get(invoice_id, ("draft", None))
        if row["entry_type"] == "void":
            result[invoice_id] = ("voided", None)
        elif row["entry_type"] == "invoice" and current_status != "voided":
            result[invoice_id] = ("posted", row["posted_at"])
    return result


async def _invoice_open_balance_map(
    session: AsyncSession, invoice_ids: list[UUID]
) -> dict[UUID, Decimal]:
    if not invoice_ids:
        return {}
    rows = (
        await session.execute(
            select(
                customer_ledger_entries.c.invoice_id,
                func.sum(customer_ledger_entries.c.amount).label("open_balance"),
            )
            .where(customer_ledger_entries.c.invoice_id.in_(invoice_ids))
            .group_by(customer_ledger_entries.c.invoice_id)
        )
    ).mappings()
    return {row["invoice_id"]: row["open_balance"] for row in rows}


async def _load_invoice(session: AsyncSession, draft_invoice_id: UUID) -> dict[str, Any]:
    invoice = (
        (
            await session.execute(
                select(draft_invoices).where(draft_invoices.c.draft_invoice_id == draft_invoice_id)
            )
        )
        .mappings()
        .one_or_none()
    )
    if invoice is None:
        raise AppError(404, "invoice_not_found", "Draft Invoice not found.")
    return dict(invoice)


async def _load_customer(session: AsyncSession, customer_id: UUID) -> dict[str, Any]:
    customer = (
        (
            await session.execute(
                select(customer_accounts).where(customer_accounts.c.customer_id == customer_id)
            )
        )
        .mappings()
        .one_or_none()
    )
    if customer is None:
        raise AppError(404, "customer_not_found", "Customer Account not found.")
    return dict(customer)


async def _update_credit_exposure(
    session: AsyncSession,
    *,
    customer_id: UUID,
    commercial_approval_id: UUID | None,
    sales_order_id: UUID | None,
    open_balance_delta: Decimal,
    approved_uninvoiced_delta: Decimal,
    source_type: str,
    source_id: UUID,
    actor_subject: str,
    correlation_id: str,
    idempotency_key: str,
) -> None:
    if open_balance_delta == ZERO and approved_uninvoiced_delta == ZERO:
        return
    exposure = (
        (
            await session.execute(
                select(customer_credit_exposure)
                .where(customer_credit_exposure.c.customer_id == customer_id)
                .with_for_update()
            )
        )
        .mappings()
        .one_or_none()
    )
    if exposure is None:
        await session.execute(
            insert(customer_credit_exposure).values(
                customer_id=customer_id,
                open_balance=open_balance_delta,
                approved_uninvoiced=approved_uninvoiced_delta,
            )
        )
    else:
        await session.execute(
            update(customer_credit_exposure)
            .where(customer_credit_exposure.c.customer_id == customer_id)
            .values(
                open_balance=customer_credit_exposure.c.open_balance + open_balance_delta,
                approved_uninvoiced=customer_credit_exposure.c.approved_uninvoiced
                + approved_uninvoiced_delta,
                version=customer_credit_exposure.c.version + 1,
                updated_at=func.now(),
            )
        )
    if open_balance_delta != ZERO:
        await session.execute(
            insert(credit_exposure_entries).values(
                entry_id=uuid4(),
                customer_id=customer_id,
                commercial_approval_id=commercial_approval_id,
                sales_order_id=sales_order_id,
                component="posted_open_balance",
                amount_delta=open_balance_delta,
                source_type=source_type,
                source_id=source_id,
                actor_subject=actor_subject,
                correlation_id=correlation_id,
                idempotency_key=f"{idempotency_key}:open_balance",
            )
        )
    if approved_uninvoiced_delta != ZERO:
        await session.execute(
            insert(credit_exposure_entries).values(
                entry_id=uuid4(),
                customer_id=customer_id,
                commercial_approval_id=commercial_approval_id,
                sales_order_id=sales_order_id,
                component="approved_uninvoiced",
                amount_delta=approved_uninvoiced_delta,
                source_type=source_type,
                source_id=source_id,
                actor_subject=actor_subject,
                correlation_id=correlation_id,
                idempotency_key=f"{idempotency_key}:approved_uninvoiced",
            )
        )


@router.post(
    "/{draft_invoice_id}/post",
    response_model=PostedInvoiceResponse,
    responses=error_responses(400, 401, 403, 404, 409, 422, 503),
    status_code=201,
)
async def post_invoice(
    draft_invoice_id: UUID,
    command: PostInvoiceCommand,
    request: Request,
    response: Response,
    actor: Annotated[AuthorizedUser, Depends(require_invoice_poster)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=200),
    ] = None,
) -> PostedInvoiceResponse:
    if idempotency_key is None:
        raise AppError(
            status_code=400,
            code="idempotency_key_required",
            message="Idempotency-Key is required for this command.",
        )

    request_hash = _hash_request(
        draft_invoice_id,
        {"posted_at": command.posted_at.isoformat() if command.posted_at else None},
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
            return PostedInvoiceResponse.model_validate(replay)

        await _lock(session, f"invoice-post:{draft_invoice_id}")
        invoice = await _load_invoice(session, draft_invoice_id)
        await _require_branch_scope(actor, invoice["branch_id"])

        status, _ = await _invoice_ledger_facts(session, draft_invoice_id)
        if status != "draft":
            raise AppError(
                status_code=409,
                code="invoice_not_postable",
                message=f"Invoice is already {status}.",
            )

        base_currency = await _base_currency(session)
        if invoice["currency"] != base_currency:
            raise AppError(
                status_code=422,
                code="unsupported_invoice_currency",
                message="Only invoices in the company base currency can be posted.",
            )

        posted_at = command.posted_at or datetime.now(UTC)
        amount = invoice["grand_total"]
        available_uninvoiced = await _available_uninvoiced(session, invoice["customer_id"])
        approved_uninvoiced_delta = -min(amount, available_uninvoiced)
        entry_id = uuid4()
        await session.execute(
            insert(customer_ledger_entries).values(
                entry_id=entry_id,
                customer_id=invoice["customer_id"],
                entry_type="invoice",
                source_type="draft_invoice",
                source_id=draft_invoice_id,
                invoice_id=draft_invoice_id,
                amount=amount,
                currency=invoice["currency"],
                branch_id=invoice["branch_id"],
                actor_subject=actor.subject,
                correlation_id=request.state.correlation_id,
                idempotency_key=idempotency_key,
                posted_at=posted_at,
            )
        )
        await _update_credit_exposure(
            session,
            customer_id=invoice["customer_id"],
            commercial_approval_id=None,
            sales_order_id=invoice["sales_order_id"],
            open_balance_delta=amount,
            approved_uninvoiced_delta=approved_uninvoiced_delta,
            source_type="draft_invoice",
            source_id=draft_invoice_id,
            actor_subject=actor.subject,
            correlation_id=request.state.correlation_id,
            idempotency_key=idempotency_key,
        )
        result = PostedInvoiceResponse(
            draft_invoice_id=draft_invoice_id,
            ledger_entry_id=entry_id,
            status="posted",
            posted_at=posted_at,
        )
        await store_command_result(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            result=result,
        )
        await auto_allocate_invoice(
            session,
            actor,
            invoice,
            request.state.correlation_id,
        )
        response.headers["X-Idempotency-Replayed"] = "false"
        return result


@router.post(
    "/{draft_invoice_id}/void",
    response_model=InvoiceVoidResponse,
    responses=error_responses(400, 401, 403, 404, 409, 422, 503),
    status_code=201,
)
async def void_invoice(
    draft_invoice_id: UUID,
    command: VoidInvoiceCommand,
    request: Request,
    response: Response,
    actor: Annotated[AuthorizedUser, Depends(require_invoice_voider)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=200),
    ] = None,
) -> InvoiceVoidResponse:
    if idempotency_key is None:
        raise AppError(
            status_code=400,
            code="idempotency_key_required",
            message="Idempotency-Key is required for this command.",
        )

    request_hash = _hash_request(draft_invoice_id, {"reason": command.reason})
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
            return InvoiceVoidResponse.model_validate(replay)

        await _lock(session, f"invoice-void:{draft_invoice_id}")
        invoice = await _load_invoice(session, draft_invoice_id)
        await _require_branch_scope(actor, invoice["branch_id"])

        status, _ = await _invoice_ledger_facts(session, draft_invoice_id)
        if status != "posted":
            raise AppError(
                status_code=409,
                code="invoice_not_voidable",
                message="Only posted invoices can be voided.",
            )

        entry_id = uuid4()
        await session.execute(
            insert(customer_ledger_entries).values(
                entry_id=entry_id,
                customer_id=invoice["customer_id"],
                entry_type="void",
                source_type="invoice_void",
                source_id=draft_invoice_id,
                invoice_id=draft_invoice_id,
                amount=-invoice["grand_total"],
                currency=invoice["currency"],
                branch_id=invoice["branch_id"],
                actor_subject=actor.subject,
                correlation_id=request.state.correlation_id,
                idempotency_key=idempotency_key,
                posted_at=datetime.now(UTC),
            )
        )
        restored_uninvoiced = await _posted_uninvoiced_reduction(session, draft_invoice_id)
        await _update_credit_exposure(
            session,
            customer_id=invoice["customer_id"],
            commercial_approval_id=None,
            sales_order_id=invoice["sales_order_id"],
            open_balance_delta=-invoice["grand_total"],
            approved_uninvoiced_delta=restored_uninvoiced,
            source_type="invoice_void",
            source_id=draft_invoice_id,
            actor_subject=actor.subject,
            correlation_id=request.state.correlation_id,
            idempotency_key=idempotency_key,
        )
        result = InvoiceVoidResponse(
            draft_invoice_id=draft_invoice_id,
            ledger_entry_id=entry_id,
            status="voided",
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
    "/{draft_invoice_id}",
    response_model=DraftInvoiceResponse,
    responses=error_responses(401, 403, 404, 503),
)
async def get_invoice(
    draft_invoice_id: UUID,
    actor: Annotated[AuthorizedUser, Depends(require_invoice_reader)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> DraftInvoiceResponse:
    invoice = await _load_invoice(session, draft_invoice_id)
    await _require_branch_scope(actor, invoice["branch_id"])
    lines = (
        (
            await session.execute(
                select(draft_invoice_lines).where(
                    draft_invoice_lines.c.draft_invoice_id == draft_invoice_id
                )
            )
        )
        .mappings()
        .all()
    )
    status, posted_at = await _invoice_ledger_facts(session, draft_invoice_id)
    open_balance = (await _invoice_open_balance_map(session, [draft_invoice_id])).get(
        draft_invoice_id, ZERO
    )
    return DraftInvoiceResponse(
        draft_invoice_id=invoice["draft_invoice_id"],
        delivery_confirmation_id=invoice["delivery_confirmation_id"],
        invoice_kind=invoice["invoice_kind"],
        status=status,
        sales_order_id=invoice["sales_order_id"],
        sales_order_revision_id=invoice["sales_order_revision_id"],
        customer_id=invoice["customer_id"],
        branch_id=invoice["branch_id"],
        currency=invoice["currency"],
        subtotal=invoice["subtotal"],
        discount_total=invoice["discount_total"],
        tax_total=invoice["tax_total"],
        grand_total=invoice["grand_total"],
        open_balance=open_balance,
        posted_at=posted_at,
        created_at=invoice["created_at"],
        lines=[
            DraftInvoiceLineResponse(
                draft_invoice_line_id=line["draft_invoice_line_id"],
                line_id=line["line_id"],
                sku_id=line["sku_id"],
                accepted_quantity_base=line["accepted_quantity_base"],
                unit_price=line["unit_price"],
                subtotal=line["subtotal"],
                discount_amount=line["discount_amount"],
                tax_amount=line["tax_amount"],
                line_total=line["line_total"],
            )
            for line in lines
        ],
    )


@router.get(
    "",
    response_model=DraftInvoiceListResponse,
    responses=error_responses(401, 403, 503),
)
async def list_invoices(
    actor: Annotated[AuthorizedUser, Depends(require_invoice_reader)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    customer_id: Annotated[UUID | None, Query()] = None,
    status: Annotated[str | None, Query()] = None,
    open_only: Annotated[bool, Query()] = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DraftInvoiceListResponse:
    query = select(draft_invoices).where(draft_invoices.c.branch_id.in_(actor.branch_ids))
    if customer_id is not None:
        query = query.where(draft_invoices.c.customer_id == customer_id)
    if open_only:
        open_invoices = (
            select(customer_ledger_entries.c.invoice_id)
            .group_by(customer_ledger_entries.c.invoice_id)
            .having(func.sum(customer_ledger_entries.c.amount) > ZERO)
            .scalar_subquery()
        )
        query = query.where(draft_invoices.c.draft_invoice_id.in_(open_invoices))

    ordered = query.order_by(draft_invoices.c.created_at.desc())
    total = (await session.scalar(select(func.count()).select_from(ordered.subquery()))) or 0
    invoices = (await session.execute(ordered.limit(limit).offset(offset))).mappings().all()

    invoice_ids = [invoice["draft_invoice_id"] for invoice in invoices]
    status_map = await _ledger_status_map(session, invoice_ids)
    open_balance_map = await _invoice_open_balance_map(session, invoice_ids)

    lines = (
        (
            await session.execute(
                select(draft_invoice_lines).where(
                    draft_invoice_lines.c.draft_invoice_id.in_(invoice_ids)
                )
            )
        )
        .mappings()
        .all()
    )
    lines_by_invoice: dict[UUID, list[Any]] = {}
    for line in lines:
        lines_by_invoice.setdefault(line["draft_invoice_id"], []).append(line)

    items: list[DraftInvoiceResponse] = []
    for invoice in invoices:
        invoice_id = invoice["draft_invoice_id"]
        invoice_status, posted_at = status_map.get(invoice_id, ("draft", None))
        if status is not None and invoice_status != status:
            continue
        open_balance = open_balance_map.get(invoice_id, ZERO)
        invoice_lines = lines_by_invoice.get(invoice_id, [])
        items.append(
            DraftInvoiceResponse(
                draft_invoice_id=invoice_id,
                delivery_confirmation_id=invoice["delivery_confirmation_id"],
                invoice_kind=invoice["invoice_kind"],
                status=invoice_status,
                sales_order_id=invoice["sales_order_id"],
                sales_order_revision_id=invoice["sales_order_revision_id"],
                customer_id=invoice["customer_id"],
                branch_id=invoice["branch_id"],
                currency=invoice["currency"],
                subtotal=invoice["subtotal"],
                discount_total=invoice["discount_total"],
                tax_total=invoice["tax_total"],
                grand_total=invoice["grand_total"],
                open_balance=open_balance,
                posted_at=posted_at,
                created_at=invoice["created_at"],
                lines=[
                    DraftInvoiceLineResponse(
                        draft_invoice_line_id=line["draft_invoice_line_id"],
                        line_id=line["line_id"],
                        sku_id=line["sku_id"],
                        accepted_quantity_base=line["accepted_quantity_base"],
                        unit_price=line["unit_price"],
                        subtotal=line["subtotal"],
                        discount_amount=line["discount_amount"],
                        tax_amount=line["tax_amount"],
                        line_total=line["line_total"],
                    )
                    for line in invoice_lines
                ],
            )
        )
    return DraftInvoiceListResponse(items=items, total=total)
