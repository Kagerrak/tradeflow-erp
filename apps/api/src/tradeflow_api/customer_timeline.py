from __future__ import annotations

from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Annotated, Any, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tradeflow_api.auth import AuthorizedUser, require_statement_reader
from tradeflow_api.database import get_database_session
from tradeflow_api.errors import AppError, error_responses
from tradeflow_api.models import (
    companies,
    credit_notes,
    customer_accounts,
    customer_ledger_entries,
    delivery_confirmations,
    delivery_dispatches,
    delivery_receipts,
    delivery_state,
    draft_invoices,
    payment_allocations,
    payment_receipt_status,
    payment_receipts,
    return_to_warehouse_receipts,
    sales_order_revisions,
    sales_orders,
    warehouses,
)

router = APIRouter(prefix="/v1/finance/customers", tags=["finance"])
ZERO = Decimal("0")

_EVENT_TYPES = {
    "order",
    "delivery",
    "return",
    "invoice",
    "invoice_void",
    "payment",
    "credit",
    "credit_reversal",
}

_EVENT_TYPE_ORDER = {
    "order": 1,
    "delivery": 2,
    "return": 3,
    "invoice": 4,
    "invoice_void": 5,
    "payment": 6,
    "credit": 7,
    "credit_reversal": 8,
}


class CustomerTimelineEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: UUID
    event_type: str
    event_at: datetime
    branch_id: UUID
    source_id: UUID
    source_type: str
    actor_subject: str
    reference_number: str | None
    amount: Decimal = Field(decimal_places=6)
    document_value: Decimal = Field(decimal_places=6)
    currency: str
    status: str | None
    metadata: dict[str, Any]


class CustomerTimelineResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_id: UUID
    currency: str
    from_date: date
    to_date: date
    as_of: date
    opening_balance: Decimal = Field(decimal_places=6)
    closing_balance: Decimal = Field(decimal_places=6)
    total: int
    items: list[CustomerTimelineEvent]


def _start_of_day(d: date) -> datetime:
    return datetime.combine(d, time.min, tzinfo=UTC)


def _end_of_day(d: date) -> datetime:
    return datetime.combine(d, time.max, tzinfo=UTC)


async def _base_currency(session: AsyncSession) -> str:
    currency = await session.scalar(select(companies.c.base_currency))
    if currency is None:
        raise AppError(503, "company_not_configured", "Company is not configured.")
    return cast(str, currency)


async def _customer_exists(session: AsyncSession, customer_id: UUID) -> None:
    exists = await session.scalar(
        select(customer_accounts.c.customer_id).where(
            customer_accounts.c.customer_id == customer_id
        )
    )
    if exists is None:
        raise AppError(404, "customer_not_found", "Customer not found.")


async def _ledger_balance(
    session: AsyncSession,
    *,
    customer_id: UUID,
    branch_ids: list[UUID],
    before_dt: datetime,
) -> Decimal:
    total = await session.scalar(
        select(func.coalesce(func.sum(customer_ledger_entries.c.amount), ZERO)).where(
            customer_ledger_entries.c.customer_id == customer_id,
            customer_ledger_entries.c.branch_id.in_(branch_ids),
            customer_ledger_entries.c.created_at <= before_dt,
        )
    )
    return cast(Decimal, total) if total is not None else ZERO


async def _order_events(
    session: AsyncSession,
    *,
    customer_id: UUID,
    branch_ids: list[UUID],
    from_dt: datetime,
    to_dt: datetime,
    salesperson_id: str | None,
    base_currency: str,
) -> list[dict[str, Any]]:
    filters = [
        sales_orders.c.customer_id == customer_id,
        sales_orders.c.branch_id.in_(branch_ids),
        sales_orders.c.created_at >= from_dt,
        sales_orders.c.created_at <= to_dt,
    ]
    if salesperson_id is not None:
        filters.append(sales_orders.c.created_by == salesperson_id)

    rows = (
        (
            await session.execute(
                select(
                    sales_orders.c.sales_order_id,
                    sales_orders.c.created_at,
                    sales_orders.c.branch_id,
                    sales_orders.c.created_by,
                    sales_orders.c.status,
                    sales_order_revisions.c.sales_order_revision_id,
                    sales_order_revisions.c.grand_total,
                    sales_order_revisions.c.currency,
                    sales_order_revisions.c.payment_timing_policy,
                )
                .select_from(
                    sales_orders.join(
                        sales_order_revisions,
                        (sales_orders.c.sales_order_id == sales_order_revisions.c.sales_order_id)
                        & (sales_orders.c.version == sales_order_revisions.c.version),
                    )
                )
                .where(*filters)
                .order_by(sales_orders.c.created_at, sales_orders.c.sales_order_id)
            )
        )
        .mappings()
        .all()
    )

    events: list[dict[str, Any]] = []
    for row in rows:
        events.append(
            {
                "event_id": row["sales_order_id"],
                "event_type": "order",
                "event_at": row["created_at"],
                "branch_id": row["branch_id"],
                "source_id": row["sales_order_id"],
                "source_type": "sales_order",
                "actor_subject": row["created_by"],
                "reference_number": None,
                "amount": ZERO,
                "document_value": Decimal(row["grand_total"]),
                "currency": row["currency"] or base_currency,
                "status": row["status"],
                "metadata": {
                    "sales_order_id": str(row["sales_order_id"]),
                    "revision_id": str(row["sales_order_revision_id"]),
                    "grand_total": str(row["grand_total"]),
                    "payment_timing_policy": row["payment_timing_policy"],
                },
            }
        )
    return events


async def _delivery_events(
    session: AsyncSession,
    *,
    customer_id: UUID,
    branch_ids: list[UUID],
    from_dt: datetime,
    to_dt: datetime,
    salesperson_id: str | None,
    base_currency: str,
) -> list[dict[str, Any]]:
    filters = [
        delivery_dispatches.c.customer_id == customer_id,
        delivery_dispatches.c.branch_id.in_(branch_ids),
        delivery_confirmations.c.confirmed_at >= from_dt,
        delivery_confirmations.c.confirmed_at <= to_dt,
    ]
    if salesperson_id is not None:
        filters.append(delivery_confirmations.c.confirmed_by == salesperson_id)

    rows = (
        (
            await session.execute(
                select(
                    delivery_confirmations.c.confirmation_id,
                    delivery_confirmations.c.confirmed_at,
                    delivery_confirmations.c.confirmed_by,
                    delivery_dispatches.c.branch_id,
                    delivery_dispatches.c.delivery_id,
                    delivery_state.c.status.label("delivery_status"),
                    delivery_receipts.c.delivery_receipt_id,
                    delivery_receipts.c.number,
                )
                .select_from(
                    delivery_confirmations.join(
                        delivery_dispatches,
                        delivery_confirmations.c.delivery_id == delivery_dispatches.c.delivery_id,
                    )
                    .join(
                        delivery_state,
                        delivery_dispatches.c.delivery_id == delivery_state.c.delivery_id,
                    )
                    .outerjoin(
                        delivery_receipts,
                        delivery_confirmations.c.confirmation_id
                        == delivery_receipts.c.confirmation_id,
                    )
                )
                .where(*filters)
                .order_by(
                    delivery_confirmations.c.confirmed_at,
                    delivery_confirmations.c.confirmation_id,
                )
            )
        )
        .mappings()
        .all()
    )

    events: list[dict[str, Any]] = []
    for row in rows:
        events.append(
            {
                "event_id": row["confirmation_id"],
                "event_type": "delivery",
                "event_at": row["confirmed_at"],
                "branch_id": row["branch_id"],
                "source_id": row["confirmation_id"],
                "source_type": "delivery_confirmation",
                "actor_subject": row["confirmed_by"],
                "reference_number": row["number"],
                "amount": ZERO,
                "document_value": ZERO,
                "currency": base_currency,
                "status": row["delivery_status"],
                "metadata": {
                    "delivery_id": str(row["delivery_id"]),
                    "confirmation_id": str(row["confirmation_id"]),
                    "receipt_id": (
                        str(row["delivery_receipt_id"]) if row["delivery_receipt_id"] else None
                    ),
                    "receipt_number": row["number"],
                },
            }
        )
    return events


async def _return_events(
    session: AsyncSession,
    *,
    customer_id: UUID,
    branch_ids: list[UUID],
    from_dt: datetime,
    to_dt: datetime,
    salesperson_id: str | None,
    base_currency: str,
) -> list[dict[str, Any]]:
    filters = [
        delivery_dispatches.c.customer_id == customer_id,
        warehouses.c.branch_id.in_(branch_ids),
        return_to_warehouse_receipts.c.created_at >= from_dt,
        return_to_warehouse_receipts.c.created_at <= to_dt,
    ]
    if salesperson_id is not None:
        filters.append(return_to_warehouse_receipts.c.received_by == salesperson_id)

    rows = (
        (
            await session.execute(
                select(
                    return_to_warehouse_receipts.c.receipt_id,
                    return_to_warehouse_receipts.c.created_at,
                    return_to_warehouse_receipts.c.received_by,
                    return_to_warehouse_receipts.c.delivery_id,
                    return_to_warehouse_receipts.c.warehouse_id,
                    warehouses.c.branch_id,
                )
                .select_from(
                    return_to_warehouse_receipts.join(
                        delivery_dispatches,
                        return_to_warehouse_receipts.c.delivery_id
                        == delivery_dispatches.c.delivery_id,
                    ).join(
                        warehouses,
                        return_to_warehouse_receipts.c.warehouse_id == warehouses.c.warehouse_id,
                    )
                )
                .where(*filters)
                .order_by(
                    return_to_warehouse_receipts.c.created_at,
                    return_to_warehouse_receipts.c.receipt_id,
                )
            )
        )
        .mappings()
        .all()
    )

    events: list[dict[str, Any]] = []
    for row in rows:
        events.append(
            {
                "event_id": row["receipt_id"],
                "event_type": "return",
                "event_at": row["created_at"],
                "branch_id": row["branch_id"],
                "source_id": row["receipt_id"],
                "source_type": "return_to_warehouse_receipt",
                "actor_subject": row["received_by"],
                "reference_number": None,
                "amount": ZERO,
                "document_value": ZERO,
                "currency": base_currency,
                "status": None,
                "metadata": {
                    "return_receipt_id": str(row["receipt_id"]),
                    "delivery_id": str(row["delivery_id"]),
                    "warehouse_id": str(row["warehouse_id"]),
                },
            }
        )
    return events


async def _invoice_events(
    session: AsyncSession,
    *,
    customer_id: UUID,
    branch_ids: list[UUID],
    from_dt: datetime,
    to_dt: datetime,
    salesperson_id: str | None,
) -> list[dict[str, Any]]:
    filters = [
        customer_ledger_entries.c.customer_id == customer_id,
        customer_ledger_entries.c.branch_id.in_(branch_ids),
        customer_ledger_entries.c.created_at >= from_dt,
        customer_ledger_entries.c.created_at <= to_dt,
        customer_ledger_entries.c.entry_type == "invoice",
        customer_ledger_entries.c.source_type == "draft_invoice",
    ]
    if salesperson_id is not None:
        filters.append(customer_ledger_entries.c.actor_subject == salesperson_id)

    rows = (
        (
            await session.execute(
                select(
                    customer_ledger_entries.c.entry_id,
                    customer_ledger_entries.c.created_at,
                    customer_ledger_entries.c.posted_at,
                    customer_ledger_entries.c.branch_id,
                    customer_ledger_entries.c.source_id,
                    customer_ledger_entries.c.amount,
                    customer_ledger_entries.c.currency,
                    customer_ledger_entries.c.actor_subject,
                    draft_invoices.c.draft_invoice_id,
                    draft_invoices.c.status,
                    draft_invoices.c.grand_total,
                    draft_invoices.c.invoice_kind,
                )
                .select_from(
                    customer_ledger_entries.join(
                        draft_invoices,
                        customer_ledger_entries.c.source_id == draft_invoices.c.draft_invoice_id,
                    )
                )
                .where(*filters)
                .order_by(customer_ledger_entries.c.created_at, customer_ledger_entries.c.entry_id)
            )
        )
        .mappings()
        .all()
    )

    events: list[dict[str, Any]] = []
    for row in rows:
        events.append(
            {
                "event_id": row["entry_id"],
                "event_type": "invoice",
                "event_at": row["created_at"],
                "branch_id": row["branch_id"],
                "source_id": row["source_id"],
                "source_type": "draft_invoice",
                "actor_subject": row["actor_subject"],
                "reference_number": None,
                "amount": Decimal(row["amount"]),
                "document_value": Decimal(row["grand_total"]),
                "currency": row["currency"],
                "status": row["status"],
                "metadata": {
                    "invoice_id": str(row["draft_invoice_id"]),
                    "invoice_kind": row["invoice_kind"],
                    "posted_at": (row["posted_at"].isoformat() if row["posted_at"] else None),
                },
            }
        )
    return events


async def _invoice_void_events(
    session: AsyncSession,
    *,
    customer_id: UUID,
    branch_ids: list[UUID],
    from_dt: datetime,
    to_dt: datetime,
    salesperson_id: str | None,
) -> list[dict[str, Any]]:
    filters = [
        customer_ledger_entries.c.customer_id == customer_id,
        customer_ledger_entries.c.branch_id.in_(branch_ids),
        customer_ledger_entries.c.created_at >= from_dt,
        customer_ledger_entries.c.created_at <= to_dt,
        customer_ledger_entries.c.entry_type == "void",
        customer_ledger_entries.c.source_type == "invoice_void",
    ]
    if salesperson_id is not None:
        filters.append(customer_ledger_entries.c.actor_subject == salesperson_id)

    rows = (
        (
            await session.execute(
                select(
                    customer_ledger_entries.c.entry_id,
                    customer_ledger_entries.c.created_at,
                    customer_ledger_entries.c.posted_at,
                    customer_ledger_entries.c.branch_id,
                    customer_ledger_entries.c.source_id,
                    customer_ledger_entries.c.amount,
                    customer_ledger_entries.c.currency,
                    customer_ledger_entries.c.actor_subject,
                    draft_invoices.c.draft_invoice_id,
                    draft_invoices.c.status,
                    draft_invoices.c.grand_total,
                )
                .select_from(
                    customer_ledger_entries.join(
                        draft_invoices,
                        customer_ledger_entries.c.source_id == draft_invoices.c.draft_invoice_id,
                    )
                )
                .where(*filters)
                .order_by(customer_ledger_entries.c.created_at, customer_ledger_entries.c.entry_id)
            )
        )
        .mappings()
        .all()
    )

    events: list[dict[str, Any]] = []
    for row in rows:
        events.append(
            {
                "event_id": row["entry_id"],
                "event_type": "invoice_void",
                "event_at": row["created_at"],
                "branch_id": row["branch_id"],
                "source_id": row["source_id"],
                "source_type": "invoice_void",
                "actor_subject": row["actor_subject"],
                "reference_number": None,
                "amount": Decimal(row["amount"]),
                "document_value": Decimal(row["grand_total"]),
                "currency": row["currency"],
                "status": "voided",
                "metadata": {
                    "invoice_id": str(row["draft_invoice_id"]),
                    "posted_at": (row["posted_at"].isoformat() if row["posted_at"] else None),
                },
            }
        )
    return events


async def _payment_events(
    session: AsyncSession,
    *,
    customer_id: UUID,
    branch_ids: list[UUID],
    from_dt: datetime,
    to_dt: datetime,
    salesperson_id: str | None,
) -> list[dict[str, Any]]:
    filters = [
        customer_ledger_entries.c.customer_id == customer_id,
        customer_ledger_entries.c.branch_id.in_(branch_ids),
        customer_ledger_entries.c.created_at >= from_dt,
        customer_ledger_entries.c.created_at <= to_dt,
        customer_ledger_entries.c.entry_type == "allocation",
        customer_ledger_entries.c.source_type == "payment_allocation",
    ]
    if salesperson_id is not None:
        filters.append(customer_ledger_entries.c.actor_subject == salesperson_id)

    rows = (
        (
            await session.execute(
                select(
                    customer_ledger_entries.c.entry_id,
                    customer_ledger_entries.c.created_at,
                    customer_ledger_entries.c.branch_id,
                    customer_ledger_entries.c.source_id,
                    customer_ledger_entries.c.amount,
                    customer_ledger_entries.c.currency,
                    customer_ledger_entries.c.actor_subject,
                    payment_allocations.c.allocation_id,
                    payment_allocations.c.payment_receipt_id,
                    payment_allocations.c.invoice_id,
                    payment_receipts.c.amount.label("receipt_amount"),
                    payment_receipts.c.payment_method_kind,
                    payment_receipt_status.c.state,
                )
                .select_from(
                    customer_ledger_entries.join(
                        payment_allocations,
                        customer_ledger_entries.c.source_id == payment_allocations.c.allocation_id,
                    )
                    .join(
                        payment_receipts,
                        payment_allocations.c.payment_receipt_id
                        == payment_receipts.c.payment_receipt_id,
                    )
                    .outerjoin(
                        payment_receipt_status,
                        payment_receipts.c.payment_receipt_id
                        == payment_receipt_status.c.payment_receipt_id,
                    )
                )
                .where(*filters)
                .order_by(customer_ledger_entries.c.created_at, customer_ledger_entries.c.entry_id)
            )
        )
        .mappings()
        .all()
    )

    events: list[dict[str, Any]] = []
    for row in rows:
        events.append(
            {
                "event_id": row["entry_id"],
                "event_type": "payment",
                "event_at": row["created_at"],
                "branch_id": row["branch_id"],
                "source_id": row["source_id"],
                "source_type": "payment_allocation",
                "actor_subject": row["actor_subject"],
                "reference_number": None,
                "amount": Decimal(row["amount"]),
                "document_value": Decimal(row["receipt_amount"]),
                "currency": row["currency"],
                "status": row["state"],
                "metadata": {
                    "allocation_id": str(row["allocation_id"]),
                    "payment_receipt_id": str(row["payment_receipt_id"]),
                    "invoice_id": str(row["invoice_id"]),
                    "payment_method": row["payment_method_kind"],
                },
            }
        )
    return events


async def _credit_events(
    session: AsyncSession,
    *,
    customer_id: UUID,
    branch_ids: list[UUID],
    from_dt: datetime,
    to_dt: datetime,
    salesperson_id: str | None,
) -> list[dict[str, Any]]:
    filters = [
        customer_ledger_entries.c.customer_id == customer_id,
        customer_ledger_entries.c.branch_id.in_(branch_ids),
        customer_ledger_entries.c.created_at >= from_dt,
        customer_ledger_entries.c.created_at <= to_dt,
        customer_ledger_entries.c.entry_type == "credit_note",
        customer_ledger_entries.c.source_type.in_(["credit_note", "credit_note_reversal"]),
    ]
    if salesperson_id is not None:
        filters.append(customer_ledger_entries.c.actor_subject == salesperson_id)

    rows = (
        (
            await session.execute(
                select(
                    customer_ledger_entries.c.entry_id,
                    customer_ledger_entries.c.created_at,
                    customer_ledger_entries.c.posted_at,
                    customer_ledger_entries.c.branch_id,
                    customer_ledger_entries.c.source_id,
                    customer_ledger_entries.c.source_type,
                    customer_ledger_entries.c.amount,
                    customer_ledger_entries.c.currency,
                    customer_ledger_entries.c.actor_subject,
                    credit_notes.c.credit_note_id,
                    credit_notes.c.amount.label("note_amount"),
                    credit_notes.c.number,
                    credit_notes.c.status,
                    credit_notes.c.reason,
                )
                .select_from(
                    customer_ledger_entries.join(
                        credit_notes,
                        customer_ledger_entries.c.source_id == credit_notes.c.credit_note_id,
                    )
                )
                .where(*filters)
                .order_by(customer_ledger_entries.c.created_at, customer_ledger_entries.c.entry_id)
            )
        )
        .mappings()
        .all()
    )

    events: list[dict[str, Any]] = []
    for row in rows:
        event_type = "credit_reversal" if row["source_type"] == "credit_note_reversal" else "credit"
        events.append(
            {
                "event_id": row["entry_id"],
                "event_type": event_type,
                "event_at": row["created_at"],
                "branch_id": row["branch_id"],
                "source_id": row["source_id"],
                "source_type": row["source_type"],
                "actor_subject": row["actor_subject"],
                "reference_number": row["number"],
                "amount": Decimal(row["amount"]),
                "document_value": Decimal(row["note_amount"]),
                "currency": row["currency"],
                "status": row["status"],
                "metadata": {
                    "credit_note_id": str(row["credit_note_id"]),
                    "reason": row["reason"],
                    "posted_at": (row["posted_at"].isoformat() if row["posted_at"] else None),
                },
            }
        )
    return events


@router.get(
    "/{customer_id}/timeline",
    response_model=CustomerTimelineResponse,
    responses=error_responses(400, 401, 403, 404, 503),
)
async def get_customer_timeline(
    customer_id: UUID,
    actor: Annotated[AuthorizedUser, Depends(require_statement_reader)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    from_date: Annotated[date, Query()],
    to_date: Annotated[date, Query()],
    as_of: Annotated[date | None, Query()] = None,
    salesperson_id: Annotated[str | None, Query(max_length=200)] = None,
    event_type: Annotated[list[str] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CustomerTimelineResponse:
    if from_date > to_date:
        raise AppError(
            400,
            "invalid_date_range",
            "from_date must be on or before to_date.",
        )

    effective_as_of = as_of or to_date
    if from_date > effective_as_of:
        raise AppError(
            400,
            "invalid_date_range",
            "from_date must be on or before as_of.",
        )

    await _customer_exists(session, customer_id)
    branch_ids = list(actor.branch_ids)
    if not branch_ids:
        raise AppError(403, "operational_scope_required", "No branch scope.")

    if event_type:
        invalid = set(event_type) - _EVENT_TYPES
        if invalid:
            raise AppError(
                400,
                "invalid_event_type",
                f"Unsupported event types: {', '.join(sorted(invalid))}.",
            )

    currency = await _base_currency(session)
    from_dt = _start_of_day(from_date)
    to_dt = _end_of_day(min(to_date, effective_as_of))
    as_of_dt = _end_of_day(effective_as_of)

    events: list[dict[str, Any]] = []
    events.extend(
        await _order_events(
            session,
            customer_id=customer_id,
            branch_ids=branch_ids,
            from_dt=from_dt,
            to_dt=to_dt,
            salesperson_id=salesperson_id,
            base_currency=currency,
        )
    )
    events.extend(
        await _delivery_events(
            session,
            customer_id=customer_id,
            branch_ids=branch_ids,
            from_dt=from_dt,
            to_dt=to_dt,
            salesperson_id=salesperson_id,
            base_currency=currency,
        )
    )
    events.extend(
        await _return_events(
            session,
            customer_id=customer_id,
            branch_ids=branch_ids,
            from_dt=from_dt,
            to_dt=to_dt,
            salesperson_id=salesperson_id,
            base_currency=currency,
        )
    )
    events.extend(
        await _invoice_events(
            session,
            customer_id=customer_id,
            branch_ids=branch_ids,
            from_dt=from_dt,
            to_dt=to_dt,
            salesperson_id=salesperson_id,
        )
    )
    events.extend(
        await _invoice_void_events(
            session,
            customer_id=customer_id,
            branch_ids=branch_ids,
            from_dt=from_dt,
            to_dt=to_dt,
            salesperson_id=salesperson_id,
        )
    )
    events.extend(
        await _payment_events(
            session,
            customer_id=customer_id,
            branch_ids=branch_ids,
            from_dt=from_dt,
            to_dt=to_dt,
            salesperson_id=salesperson_id,
        )
    )
    events.extend(
        await _credit_events(
            session,
            customer_id=customer_id,
            branch_ids=branch_ids,
            from_dt=from_dt,
            to_dt=to_dt,
            salesperson_id=salesperson_id,
        )
    )

    if event_type:
        allowed = set(event_type)
        events = [event for event in events if event["event_type"] in allowed]

    events.sort(
        key=lambda event: (
            event["event_at"],
            _EVENT_TYPE_ORDER[event["event_type"]],
            str(event["source_id"]),
        )
    )

    total = len(events)
    page = events[offset : offset + limit]

    opening = await _ledger_balance(
        session,
        customer_id=customer_id,
        branch_ids=branch_ids,
        before_dt=min(from_dt, as_of_dt),
    )
    closing = await _ledger_balance(
        session,
        customer_id=customer_id,
        branch_ids=branch_ids,
        before_dt=as_of_dt,
    )

    return CustomerTimelineResponse(
        customer_id=customer_id,
        currency=currency,
        from_date=from_date,
        to_date=to_date,
        as_of=effective_as_of,
        opening_balance=opening,
        closing_balance=closing,
        total=total,
        items=[CustomerTimelineEvent.model_validate(event) for event in page],
    )
