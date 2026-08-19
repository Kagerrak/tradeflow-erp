from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import case, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession

from tradeflow_api.auth import AuthorizedUser, require_statement_reader
from tradeflow_api.database import get_database_session
from tradeflow_api.errors import AppError, error_responses
from tradeflow_api.models import (
    companies,
    customer_accounts,
    customer_ledger_entries,
    payment_allocations,
    payment_receipt_events,
    payment_receipts,
)
from tradeflow_api.payment_balance import (
    PaymentApplicationState,
    payment_application_state,
    unapplied_payment_amount,
)

router = APIRouter(prefix="/v1/finance/customers", tags=["finance"])
ZERO = Decimal("0")


class StatementLine(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entry_id: UUID
    posted_at: date | None
    entry_type: str
    source_type: str
    source_id: UUID
    invoice_id: UUID | None
    amount: Decimal = Field(decimal_places=6)
    running_balance: Decimal = Field(decimal_places=6)


class StatementDocument(BaseModel):
    model_config = ConfigDict(extra="forbid")

    invoice_id: UUID
    posted_at: date | None
    original_amount: Decimal = Field(decimal_places=6)
    paid_amount: Decimal = Field(decimal_places=6)
    open_amount: Decimal = Field(decimal_places=6)
    state: str
    aging_bucket: str


class StatementUnappliedPayment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payment_receipt_id: UUID
    received_at: date
    payment_method: str
    amount: Decimal = Field(decimal_places=6)
    allocated_amount: Decimal = Field(decimal_places=6)
    unapplied_amount: Decimal = Field(decimal_places=6)
    application_state: PaymentApplicationState


class StatementResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: UUID
    currency: str
    from_date: date
    to_date: date
    as_of: date
    opening_balance: Decimal = Field(decimal_places=6)
    closing_balance: Decimal = Field(decimal_places=6)
    unapplied_payment_total: Decimal = Field(decimal_places=6)
    lines: list[StatementLine]
    documents: list[StatementDocument]
    unapplied_payments: list[StatementUnappliedPayment]


def _start_of_day(d: date) -> datetime:
    return datetime.combine(d, time.min, tzinfo=UTC)


def _end_of_day(d: date) -> datetime:
    return datetime.combine(d, time.max, tzinfo=UTC)


def _aging_bucket(days_overdue: int) -> str:
    if days_overdue <= 0:
        return "current"
    if days_overdue <= 30:
        return "1-30"
    if days_overdue <= 60:
        return "31-60"
    if days_overdue <= 90:
        return "61-90"
    return "90+"


def _document_state(
    *,
    original: Decimal,
    paid: Decimal,
    credited: Decimal,
    voided: Decimal,
    days_overdue: int,
) -> str:
    open_amount = original - paid - credited - voided
    if open_amount <= ZERO:
        if credited + voided >= original:
            return "credited"
        return "paid"
    if paid > ZERO:
        return "partially_paid"
    if days_overdue > 0:
        return "overdue"
    return "unpaid"


async def _customer_currency(session: AsyncSession, customer_id: UUID) -> str:
    customer_exists = await session.scalar(
        select(customer_accounts.c.customer_id).where(
            customer_accounts.c.customer_id == customer_id
        )
    )
    if customer_exists is None:
        raise AppError(404, "customer_not_found", "Customer not found.")
    base_currency = await session.scalar(select(companies.c.base_currency).limit(1))
    return cast(str, base_currency) if base_currency is not None else "PHP"


async def _ledger_entries(
    session: AsyncSession,
    *,
    customer_id: UUID,
    branch_ids: list[UUID],
    from_dt: datetime,
    to_dt: datetime,
) -> list[Mapping[str, Any]]:
    rows = (
        (
            await session.execute(
                select(customer_ledger_entries)
                .where(
                    customer_ledger_entries.c.customer_id == customer_id,
                    customer_ledger_entries.c.branch_id.in_(branch_ids),
                    customer_ledger_entries.c.created_at >= from_dt,
                    customer_ledger_entries.c.created_at <= to_dt,
                )
                .order_by(
                    customer_ledger_entries.c.created_at,
                    customer_ledger_entries.c.entry_id,
                )
            )
        )
        .mappings()
        .all()
    )
    return [dict(row) for row in rows]


async def _opening_balance(
    session: AsyncSession,
    *,
    customer_id: UUID,
    branch_ids: list[UUID],
    from_dt: datetime,
) -> Decimal:
    total = await session.scalar(
        select(func.coalesce(func.sum(customer_ledger_entries.c.amount), ZERO)).where(
            customer_ledger_entries.c.customer_id == customer_id,
            customer_ledger_entries.c.branch_id.in_(branch_ids),
            customer_ledger_entries.c.created_at < from_dt,
        )
    )
    return cast(Decimal, total) if total is not None else ZERO


def _build_documents(
    entries: list[Mapping[str, Any]],
    as_of: date,
) -> list[StatementDocument]:
    by_invoice: dict[UUID, dict[str, Any]] = {}
    for entry in entries:
        invoice_id = entry["invoice_id"]
        if invoice_id is None:
            continue
        if invoice_id not in by_invoice:
            by_invoice[invoice_id] = {
                "posted_at": entry.get("posted_at") or entry["created_at"],
                "invoice": ZERO,
                "allocation": ZERO,
                "credit_note": ZERO,
                "void": ZERO,
            }
        item = by_invoice[invoice_id]
        entry_type = entry["entry_type"]
        amount = Decimal(entry["amount"])
        if entry_type == "invoice":
            item["invoice"] += amount
            if item["posted_at"] is None:
                item["posted_at"] = entry.get("posted_at") or entry["created_at"]
        elif entry_type == "allocation":
            item["allocation"] += abs(amount)
        elif entry_type == "credit_note":
            item["credit_note"] += abs(amount)
        elif entry_type == "void":
            item["void"] += abs(amount)

    documents: list[StatementDocument] = []
    for invoice_id, item in by_invoice.items():
        original = item["invoice"]
        if original <= ZERO:
            continue
        posted_dt = item["posted_at"]
        if isinstance(posted_dt, datetime):
            posted_date = posted_dt.date()
        else:
            posted_date = posted_dt
        days_overdue = (as_of - posted_date).days
        paid = item["allocation"]
        credited = item["credit_note"]
        voided = item["void"]
        open_amount = original - paid - credited - voided
        state = _document_state(
            original=original,
            paid=paid,
            credited=credited,
            voided=voided,
            days_overdue=days_overdue,
        )
        # Aging reflects the open balance; paid/credited documents are current.
        aging_bucket = "current" if open_amount <= ZERO else _aging_bucket(days_overdue)
        documents.append(
            StatementDocument(
                invoice_id=invoice_id,
                posted_at=posted_date,
                original_amount=original,
                paid_amount=paid,
                open_amount=open_amount,
                state=state,
                aging_bucket=aging_bucket,
            )
        )
    return documents


async def _unapplied_payments(
    session: AsyncSession,
    *,
    customer_id: UUID,
    branch_ids: list[UUID],
    as_of: date,
) -> list[StatementUnappliedPayment]:
    as_of_dt = _end_of_day(as_of)
    cleared = (
        select(payment_receipt_events.c.payment_receipt_event_id)
        .where(
            payment_receipt_events.c.payment_receipt_id == payment_receipts.c.payment_receipt_id,
            payment_receipt_events.c.event_type == "cleared",
            payment_receipt_events.c.occurred_at <= as_of_dt,
        )
        .exists()
    )
    reversed_or_refunded = (
        select(payment_receipt_events.c.payment_receipt_event_id)
        .where(
            payment_receipt_events.c.payment_receipt_id == payment_receipts.c.payment_receipt_id,
            payment_receipt_events.c.event_type.in_(("reversed", "refunded")),
            payment_receipt_events.c.occurred_at <= as_of_dt,
        )
        .exists()
    )
    allocated = (
        select(func.coalesce(func.sum(payment_allocations.c.amount), ZERO))
        .where(
            payment_allocations.c.payment_receipt_id == payment_receipts.c.payment_receipt_id,
            payment_allocations.c.created_at <= as_of_dt,
        )
        .scalar_subquery()
    )
    rows = (
        (
            await session.execute(
                select(
                    payment_receipts.c.payment_receipt_id,
                    payment_receipts.c.received_at,
                    payment_receipts.c.payment_method_kind,
                    payment_receipts.c.amount,
                    case((cleared & ~reversed_or_refunded, "cleared"), else_="not_cleared").label(
                        "state"
                    ),
                    case((cleared, payment_receipts.c.amount), else_=ZERO).label("cleared_amount"),
                    case((reversed_or_refunded, payment_receipts.c.amount), else_=ZERO).label(
                        "reversed_amount"
                    ),
                    literal(ZERO).label("refunded_amount"),
                    allocated.label("allocated_amount"),
                )
                .where(
                    payment_receipts.c.customer_id == customer_id,
                    payment_receipts.c.branch_id.in_(branch_ids),
                    payment_receipts.c.received_at <= as_of_dt,
                    cleared,
                    ~reversed_or_refunded,
                    payment_receipts.c.amount - allocated > ZERO,
                )
                .order_by(
                    payment_receipts.c.received_at,
                    payment_receipts.c.payment_receipt_id,
                )
            )
        )
        .mappings()
        .all()
    )
    return [
        StatementUnappliedPayment(
            payment_receipt_id=row["payment_receipt_id"],
            received_at=row["received_at"].date(),
            payment_method=row["payment_method_kind"],
            amount=row["amount"],
            allocated_amount=row["allocated_amount"],
            unapplied_amount=unapplied_payment_amount(dict(row)),
            application_state=payment_application_state(dict(row)),
        )
        for row in rows
    ]


@router.get(
    "/{customer_id}/statement",
    response_model=StatementResponse,
    responses=error_responses(400, 401, 403, 404, 503),
)
async def get_customer_statement(
    customer_id: UUID,
    actor: Annotated[AuthorizedUser, Depends(require_statement_reader)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    from_date: Annotated[date, Query()],
    to_date: Annotated[date, Query()],
    as_of: Annotated[date | None, Query()] = None,
) -> StatementResponse:
    if from_date > to_date:
        raise AppError(
            400,
            "invalid_date_range",
            "from_date must be on or before to_date.",
        )
    effective_as_of = as_of or to_date
    currency = await _customer_currency(session, customer_id)
    branch_ids = list(actor.branch_ids)
    if not branch_ids:
        raise AppError(403, "operational_scope_required", "No branch scope.")

    from_dt = _start_of_day(from_date)
    to_dt = _end_of_day(to_date)
    opening = await _opening_balance(
        session,
        customer_id=customer_id,
        branch_ids=branch_ids,
        from_dt=from_dt,
    )
    entries = await _ledger_entries(
        session,
        customer_id=customer_id,
        branch_ids=branch_ids,
        from_dt=from_dt,
        to_dt=to_dt,
    )

    running = opening
    lines: list[StatementLine] = []
    for entry in entries:
        running += Decimal(entry["amount"])
        posted_at_value = entry.get("posted_at")
        posted_at = (
            posted_at_value.date() if isinstance(posted_at_value, datetime) else posted_at_value
        )
        lines.append(
            StatementLine(
                entry_id=entry["entry_id"],
                posted_at=posted_at,
                entry_type=entry["entry_type"],
                source_type=entry["source_type"],
                source_id=entry["source_id"],
                invoice_id=entry["invoice_id"],
                amount=Decimal(entry["amount"]),
                running_balance=running,
            )
        )

    documents = _build_documents(entries, effective_as_of)
    unapplied_payments = await _unapplied_payments(
        session,
        customer_id=customer_id,
        branch_ids=branch_ids,
        as_of=effective_as_of,
    )

    return StatementResponse(
        customer_id=customer_id,
        currency=currency,
        from_date=from_date,
        to_date=to_date,
        as_of=effective_as_of,
        opening_balance=opening,
        closing_balance=running,
        unapplied_payment_total=sum(
            (payment.unapplied_amount for payment in unapplied_payments), ZERO
        ),
        lines=lines,
        documents=documents,
        unapplied_payments=unapplied_payments,
    )
