from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from typing import Annotated, Any, cast
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Header, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import case, func, insert, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from tradeflow_api.auth import (
    AuthorizedUser,
    require_payment_allocator,
    require_payment_projection_rebuilder,
    require_payment_reader,
)
from tradeflow_api.command_receipts import get_command_replay, store_command_result
from tradeflow_api.database import get_database_session
from tradeflow_api.errors import AppError, error_responses
from tradeflow_api.models import (
    cod_collections,
    credit_exposure_entries,
    customer_credit_exposure,
    customer_ledger_entries,
    delivery_confirmations,
    delivery_dispatches,
    draft_invoices,
    payment_allocations,
    payment_receipt_balances,
    payment_receipt_status,
    payment_receipts,
    prepayment_coverage_events,
)
from tradeflow_api.payment_balance import (
    PaymentApplicationState,
    payment_application_state,
    unapplied_payment_amount,
)

router = APIRouter(
    prefix="/v1/finance/payment-receipts",
    tags=["finance"],
)
ZERO = Decimal("0")


class CommandModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AllocationApplication(CommandModel):
    invoice_id: UUID
    amount: Decimal = Field(gt=0, max_digits=24, decimal_places=6)


class AllocatePaymentCommand(CommandModel):
    expected_version: int = Field(ge=1)
    allocations: list[AllocationApplication] = Field(min_length=1)


class AllocationResponse(BaseModel):
    allocation_id: UUID
    payment_receipt_id: UUID
    invoice_id: UUID
    amount: Decimal
    currency: str
    created_at: datetime


class AllocationCommandResult(BaseModel):
    allocations: list[AllocationResponse]


class AppliedAllocation(BaseModel):
    allocation_id: UUID
    invoice_id: UUID
    amount: Decimal
    allocated_at: datetime


class PaymentReceiptAllocationListResponse(BaseModel):
    payment_receipt_id: UUID
    cleared_amount: Decimal
    allocated_amount: Decimal
    available_amount: Decimal
    coverage_designated_amount: Decimal
    application_state: PaymentApplicationState
    version: int
    allocations: list[AppliedAllocation]


class PaymentProjectionRebuildResponse(BaseModel):
    receipt_rows: int
    allocated_total: Decimal
    unapplied_total: Decimal


def _hash_request(payment_receipt_id: UUID, body: dict[str, Any]) -> str:
    payload = {"payment_receipt_id": str(payment_receipt_id), **body}
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


async def _receipt_branch_id(session: AsyncSession, payment_receipt_id: UUID) -> UUID:
    branch_id = await session.scalar(
        select(payment_receipts.c.branch_id).where(
            payment_receipts.c.payment_receipt_id == payment_receipt_id
        )
    )
    if branch_id is None:
        raise AppError(404, "payment_receipt_not_found", "Payment Receipt not found.")
    return cast(UUID, branch_id)


async def _load_receipt(session: AsyncSession, payment_receipt_id: UUID) -> dict[str, Any]:
    row = (
        (
            await session.execute(
                select(
                    payment_receipts,
                    payment_receipt_status.c.state,
                    payment_receipt_balances.c.cleared_amount,
                    payment_receipt_balances.c.reversed_amount,
                    payment_receipt_balances.c.refunded_amount,
                    payment_receipt_balances.c.allocated_amount,
                    payment_receipt_balances.c.coverage_designated_amount,
                    payment_receipt_balances.c.version.label("balance_version"),
                )
                .join(
                    payment_receipt_status,
                    payment_receipts.c.payment_receipt_id
                    == payment_receipt_status.c.payment_receipt_id,
                )
                .join(
                    payment_receipt_balances,
                    payment_receipts.c.payment_receipt_id
                    == payment_receipt_balances.c.payment_receipt_id,
                )
                .where(payment_receipts.c.payment_receipt_id == payment_receipt_id)
                .with_for_update()
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        raise AppError(404, "payment_receipt_not_found", "Payment Receipt not found.")
    if row["state"] != "cleared":
        raise AppError(
            409,
            "payment_receipt_not_cleared",
            "Only cleared receipts can be allocated.",
        )
    return dict(row)


async def _load_invoice(session: AsyncSession, invoice_id: UUID) -> dict[str, Any]:
    invoice = (
        (
            await session.execute(
                select(draft_invoices).where(draft_invoices.c.draft_invoice_id == invoice_id)
            )
        )
        .mappings()
        .one_or_none()
    )
    if invoice is None:
        raise AppError(404, "invoice_not_found", "Draft Invoice not found.")
    return dict(invoice)


async def _invoice_open_balance(session: AsyncSession, invoice_id: UUID) -> Decimal:
    total = await session.scalar(
        select(func.coalesce(func.sum(customer_ledger_entries.c.amount), ZERO)).where(
            customer_ledger_entries.c.invoice_id == invoice_id
        )
    )
    return cast(Decimal, total) if total is not None else ZERO


async def _invoice_status(session: AsyncSession, invoice_id: UUID) -> str:
    rows = (
        (
            await session.execute(
                select(customer_ledger_entries.c.entry_type).where(
                    customer_ledger_entries.c.invoice_id == invoice_id
                )
            )
        )
        .scalars()
        .all()
    )
    if "void" in rows:
        return "voided"
    if "invoice" in rows:
        return "posted"
    return "draft"


def _available_receipt_balance(receipt: Mapping[str, Any]) -> Decimal:
    return unapplied_payment_amount(receipt) - Decimal(receipt["coverage_designated_amount"])


async def _update_credit_exposure(
    session: AsyncSession,
    *,
    customer_id: UUID,
    open_balance_delta: Decimal,
    source_id: UUID,
    actor_subject: str,
    correlation_id: str,
    idempotency_key: str,
) -> None:
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
        raise AppError(409, "customer_exposure_missing", "Customer exposure not found.")
    new_open_balance = exposure["open_balance"] + open_balance_delta
    await session.execute(
        update(customer_credit_exposure)
        .where(customer_credit_exposure.c.customer_id == customer_id)
        .values(
            open_balance=new_open_balance,
            version=customer_credit_exposure.c.version + 1,
            updated_at=func.now(),
        )
    )
    await session.execute(
        insert(credit_exposure_entries).values(
            entry_id=uuid4(),
            customer_id=customer_id,
            component="posted_open_balance",
            amount_delta=open_balance_delta,
            source_type="payment_allocation",
            source_id=source_id,
            actor_subject=actor_subject,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            created_at=datetime.now(UTC),
        )
    )


async def _allocate(
    session: AsyncSession,
    *,
    actor: AuthorizedUser,
    receipt: dict[str, Any],
    invoice: Mapping[str, Any],
    amount: Decimal,
    correlation_id: str,
    idempotency_key: str,
    reduce_coverage_by: Decimal = ZERO,
) -> dict[str, Any]:
    allocation_id = uuid4()
    await session.execute(
        insert(payment_allocations).values(
            allocation_id=allocation_id,
            payment_receipt_id=receipt["payment_receipt_id"],
            invoice_id=invoice["draft_invoice_id"],
            amount=amount,
            currency=invoice["currency"],
            branch_id=invoice["branch_id"],
            actor_subject=actor.subject,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
        )
    )
    await session.execute(
        insert(customer_ledger_entries).values(
            entry_id=uuid4(),
            customer_id=invoice["customer_id"],
            entry_type="allocation",
            source_type="payment_allocation",
            source_id=allocation_id,
            invoice_id=invoice["draft_invoice_id"],
            amount=-amount,
            currency=invoice["currency"],
            branch_id=invoice["branch_id"],
            actor_subject=actor.subject,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
            created_at=datetime.now(UTC),
        )
    )
    new_allocated = receipt["allocated_amount"] + amount
    new_coverage = receipt["coverage_designated_amount"] - reduce_coverage_by
    new_version = receipt["balance_version"] + 1
    await session.execute(
        update(payment_receipt_balances)
        .where(payment_receipt_balances.c.payment_receipt_id == receipt["payment_receipt_id"])
        .values(
            allocated_amount=new_allocated,
            coverage_designated_amount=new_coverage,
            version=new_version,
        )
    )
    receipt["allocated_amount"] = new_allocated
    receipt["coverage_designated_amount"] = new_coverage
    receipt["balance_version"] = new_version
    await _update_credit_exposure(
        session,
        customer_id=invoice["customer_id"],
        open_balance_delta=-amount,
        source_id=allocation_id,
        actor_subject=actor.subject,
        correlation_id=correlation_id,
        idempotency_key=idempotency_key,
    )
    return {
        "allocation_id": allocation_id,
        "payment_receipt_id": receipt["payment_receipt_id"],
        "invoice_id": invoice["draft_invoice_id"],
        "amount": amount,
        "currency": invoice["currency"],
        "created_at": datetime.now(UTC),
    }


async def _linked_fulfillment_order_id(session: AsyncSession, confirmation_id: UUID) -> UUID | None:
    row = (
        (
            await session.execute(
                select(delivery_dispatches.c.fulfillment_order_id)
                .join(
                    delivery_confirmations,
                    delivery_confirmations.c.delivery_id == delivery_dispatches.c.delivery_id,
                )
                .where(delivery_confirmations.c.confirmation_id == confirmation_id)
            )
        )
        .mappings()
        .one_or_none()
    )
    if row is None:
        return None
    return cast(UUID, row["fulfillment_order_id"])


async def _auto_allocate(
    session: AsyncSession,
    actor: AuthorizedUser,
    invoice: Mapping[str, Any],
    correlation_id: str,
) -> list[dict[str, Any]]:
    remaining = await _invoice_open_balance(session, invoice["draft_invoice_id"])
    if remaining <= ZERO:
        return []

    allocations: list[dict[str, Any]] = []

    # COD receipts captured for this delivery confirmation.
    cod_receipts = (
        (
            await session.execute(
                select(
                    payment_receipts.c.payment_receipt_id,
                    payment_receipt_balances.c.cleared_amount,
                    payment_receipt_balances.c.reversed_amount,
                    payment_receipt_balances.c.refunded_amount,
                    payment_receipt_balances.c.allocated_amount,
                    payment_receipt_balances.c.coverage_designated_amount,
                    payment_receipt_balances.c.version.label("balance_version"),
                )
                .join(
                    cod_collections,
                    cod_collections.c.payment_receipt_id == payment_receipts.c.payment_receipt_id,
                )
                .join(
                    payment_receipt_status,
                    payment_receipt_status.c.payment_receipt_id
                    == payment_receipts.c.payment_receipt_id,
                )
                .join(
                    payment_receipt_balances,
                    payment_receipt_balances.c.payment_receipt_id
                    == payment_receipts.c.payment_receipt_id,
                )
                .where(
                    cod_collections.c.confirmation_id == invoice["delivery_confirmation_id"],
                    payment_receipt_status.c.state == "cleared",
                    payment_receipts.c.customer_id == invoice["customer_id"],
                    payment_receipts.c.branch_id == invoice["branch_id"],
                )
                .order_by(payment_receipts.c.created_at)
                .with_for_update()
            )
        )
        .mappings()
        .all()
    )

    for row in cod_receipts:
        if remaining <= ZERO:
            break
        receipt = dict(row)
        available = _available_receipt_balance(receipt)
        if available <= ZERO:
            continue
        apply_amount = min(available, remaining)
        allocations.append(
            await _allocate(
                session,
                actor=actor,
                receipt=receipt,
                invoice=invoice,
                amount=apply_amount,
                correlation_id=correlation_id,
                idempotency_key=f"auto:{correlation_id}:{receipt['payment_receipt_id']}",
            )
        )
        remaining -= apply_amount

    if remaining <= ZERO:
        return allocations

    fulfillment_order_id = await _linked_fulfillment_order_id(
        session, invoice["delivery_confirmation_id"]
    )
    if fulfillment_order_id is None:
        return allocations

    # Prepayment coverage designated for the linked fulfillment order.
    covered_receipt_ids = (
        (
            await session.execute(
                select(
                    prepayment_coverage_events.c.payment_receipt_id,
                    func.coalesce(
                        func.sum(
                            case(
                                (
                                    prepayment_coverage_events.c.event_type == "designated",
                                    prepayment_coverage_events.c.amount,
                                ),
                                else_=-prepayment_coverage_events.c.amount,
                            )
                        ),
                        ZERO,
                    ).label("designated_amount"),
                )
                .where(
                    prepayment_coverage_events.c.fulfillment_order_id == fulfillment_order_id,
                )
                .group_by(prepayment_coverage_events.c.payment_receipt_id)
                .having(
                    func.coalesce(
                        func.sum(
                            case(
                                (
                                    prepayment_coverage_events.c.event_type == "designated",
                                    prepayment_coverage_events.c.amount,
                                ),
                                else_=-prepayment_coverage_events.c.amount,
                            )
                        ),
                        ZERO,
                    )
                    > ZERO
                )
                .order_by(prepayment_coverage_events.c.payment_receipt_id)
            )
        )
        .mappings()
        .all()
    )

    for row in covered_receipt_ids:
        if remaining <= ZERO:
            break
        receipt = await _load_receipt(session, row["payment_receipt_id"])
        if receipt["state"] != "cleared":
            continue
        if receipt["customer_id"] != invoice["customer_id"]:
            continue
        if receipt["branch_id"] != invoice["branch_id"]:
            continue
        available = _available_receipt_balance(receipt)
        if available <= ZERO:
            continue
        designated = row["designated_amount"]
        apply_amount = min(available, remaining, designated)
        if apply_amount <= ZERO:
            continue
        coverage_to_consume = min(designated, apply_amount)
        await session.execute(
            insert(prepayment_coverage_events).values(
                coverage_event_id=uuid4(),
                fulfillment_order_id=fulfillment_order_id,
                payment_receipt_id=receipt["payment_receipt_id"],
                event_type="consumed",
                amount=coverage_to_consume,
                reason="Auto-allocated to posted invoice",
                actor_subject=actor.subject,
                source_id=invoice["draft_invoice_id"],
                correlation_id=correlation_id,
                idempotency_key=f"auto-coverage:{correlation_id}:{receipt['payment_receipt_id']}",
            )
        )
        allocations.append(
            await _allocate(
                session,
                actor=actor,
                receipt=receipt,
                invoice=invoice,
                amount=apply_amount,
                correlation_id=correlation_id,
                idempotency_key=f"auto:{correlation_id}:{receipt['payment_receipt_id']}",
                reduce_coverage_by=coverage_to_consume,
            )
        )
        remaining -= apply_amount

    return allocations


@router.post(
    "/projections/rebuild",
    response_model=PaymentProjectionRebuildResponse,
    responses=error_responses(401, 403, 500),
)
async def rebuild_payment_receipt_projections(
    actor: Annotated[AuthorizedUser, Depends(require_payment_projection_rebuilder)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> PaymentProjectionRebuildResponse:
    if not actor.branch_ids:
        return PaymentProjectionRebuildResponse(
            receipt_rows=0,
            allocated_total=ZERO,
            unapplied_total=ZERO,
        )
    await session.rollback()
    async with session.begin():
        await session.execute(
            text(
                "LOCK TABLE payment_receipts, payment_receipt_events, "
                "payment_allocations, prepayment_coverage_events, "
                "payment_receipt_balances IN SHARE ROW EXCLUSIVE MODE"
            )
        )
        await session.execute(
            text(
                """
                WITH scoped_receipts AS (
                  SELECT receipt.payment_receipt_id, receipt.amount
                  FROM payment_receipts receipt
                  WHERE receipt.branch_id = ANY(:branch_ids)
                ), allocation_totals AS (
                  SELECT allocation.payment_receipt_id,
                         sum(allocation.amount) AS allocated_amount
                  FROM payment_allocations allocation
                  JOIN scoped_receipts receipt USING (payment_receipt_id)
                  GROUP BY allocation.payment_receipt_id
                ), coverage_totals AS (
                  SELECT event.payment_receipt_id,
                         greatest(sum(CASE
                           WHEN event.event_type = 'designated' THEN event.amount
                           ELSE -event.amount
                         END), 0) AS coverage_designated_amount
                  FROM prepayment_coverage_events event
                  JOIN scoped_receipts receipt USING (payment_receipt_id)
                  GROUP BY event.payment_receipt_id
                ), facts AS (
                  SELECT receipt.payment_receipt_id,
                    CASE WHEN EXISTS (
                      SELECT 1 FROM payment_receipt_events event
                      WHERE event.payment_receipt_id = receipt.payment_receipt_id
                        AND event.event_type = 'cleared'
                    ) THEN receipt.amount ELSE 0 END AS cleared_amount,
                    CASE WHEN EXISTS (
                      SELECT 1 FROM payment_receipt_events event
                      WHERE event.payment_receipt_id = receipt.payment_receipt_id
                        AND event.event_type = 'reversed'
                    ) THEN receipt.amount ELSE 0 END AS reversed_amount,
                    CASE WHEN EXISTS (
                      SELECT 1 FROM payment_receipt_events event
                      WHERE event.payment_receipt_id = receipt.payment_receipt_id
                        AND event.event_type = 'refunded'
                    ) THEN receipt.amount ELSE 0 END AS refunded_amount,
                    coalesce(allocation.allocated_amount, 0) AS allocated_amount,
                    coalesce(coverage.coverage_designated_amount, 0)
                      AS coverage_designated_amount
                  FROM scoped_receipts receipt
                  LEFT JOIN allocation_totals allocation USING (payment_receipt_id)
                  LEFT JOIN coverage_totals coverage USING (payment_receipt_id)
                )
                INSERT INTO payment_receipt_balances (
                  payment_receipt_id, cleared_amount, reversed_amount,
                  refunded_amount, allocated_amount, coverage_designated_amount,
                  version
                )
                SELECT payment_receipt_id, cleared_amount, reversed_amount,
                       refunded_amount, allocated_amount,
                       coverage_designated_amount, 1
                FROM facts
                ON CONFLICT (payment_receipt_id) DO UPDATE SET
                  cleared_amount = excluded.cleared_amount,
                  reversed_amount = excluded.reversed_amount,
                  refunded_amount = excluded.refunded_amount,
                  allocated_amount = excluded.allocated_amount,
                  coverage_designated_amount = excluded.coverage_designated_amount,
                  version = payment_receipt_balances.version + 1
                """
            ),
            {"branch_ids": list(actor.branch_ids)},
        )
        totals = (
            (
                await session.execute(
                    select(
                        func.count().label("receipt_rows"),
                        func.coalesce(
                            func.sum(payment_receipt_balances.c.allocated_amount), ZERO
                        ).label("allocated_total"),
                        func.coalesce(
                            func.sum(
                                payment_receipt_balances.c.cleared_amount
                                - payment_receipt_balances.c.reversed_amount
                                - payment_receipt_balances.c.refunded_amount
                                - payment_receipt_balances.c.allocated_amount
                            ),
                            ZERO,
                        ).label("unapplied_total"),
                    )
                    .select_from(
                        payment_receipt_balances.join(
                            payment_receipts,
                            payment_receipts.c.payment_receipt_id
                            == payment_receipt_balances.c.payment_receipt_id,
                        )
                    )
                    .where(payment_receipts.c.branch_id.in_(actor.branch_ids))
                )
            )
            .mappings()
            .one()
        )
    return PaymentProjectionRebuildResponse(**totals)


@router.post(
    "/{payment_receipt_id}/allocations",
    response_model=list[AllocationResponse],
    status_code=201,
    responses=error_responses(400, 401, 403, 404, 409, 503),
)
async def allocate_payment(
    payment_receipt_id: UUID,
    command: AllocatePaymentCommand,
    request: Request,
    response: Response,
    actor: Annotated[AuthorizedUser, Depends(require_payment_allocator)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
    idempotency_key: Annotated[
        str | None,
        Header(alias="Idempotency-Key", min_length=1, max_length=200),
    ] = None,
) -> list[AllocationResponse]:
    if idempotency_key is None:
        raise AppError(400, "idempotency_key_required", "Idempotency-Key is required.")
    hash_key = _hash_request(payment_receipt_id, command.model_dump())
    await session.rollback()
    async with session.begin():
        replay = await get_command_replay(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=hash_key,
        )
        if replay is not None:
            branch_id = await _receipt_branch_id(session, payment_receipt_id)
            await _require_branch_scope(actor, branch_id)
            response.status_code = 200
            response.headers["X-Idempotency-Replayed"] = "true"
            return AllocationCommandResult.model_validate(replay).allocations

        await _lock(session, f"payment-allocation:{payment_receipt_id}")

        receipt = await _load_receipt(session, payment_receipt_id)
        await _require_branch_scope(actor, receipt["branch_id"])
        if receipt["balance_version"] != command.expected_version:
            raise AppError(
                409,
                "payment_balance_version_conflict",
                "The Payment Receipt balance changed and requires refresh.",
                details={
                    "expected_version": command.expected_version,
                    "current_version": receipt["balance_version"],
                },
            )

        total = sum(allocation.amount for allocation in command.allocations)
        available = _available_receipt_balance(receipt)
        if total > available:
            raise AppError(
                409,
                "payment_receipt_overallocated",
                "Allocation exceeds the receipt's available cleared value.",
            )

        allocations: list[dict[str, Any]] = []
        for application in command.allocations:
            invoice = await _load_invoice(session, application.invoice_id)
            await _require_branch_scope(actor, invoice["branch_id"])
            if invoice["customer_id"] != receipt["customer_id"]:
                raise AppError(
                    409,
                    "allocation_customer_mismatch",
                    "Receipt and invoice must belong to the same customer.",
                )
            status = await _invoice_status(session, invoice["draft_invoice_id"])
            if status != "posted":
                raise AppError(
                    409,
                    "invoice_not_posted",
                    "Only posted invoices can receive payment allocations.",
                )
            open_balance = await _invoice_open_balance(session, invoice["draft_invoice_id"])
            if application.amount > open_balance:
                raise AppError(
                    409,
                    "invoice_overallocated",
                    "Allocation exceeds the invoice's open balance.",
                )
            allocations.append(
                await _allocate(
                    session,
                    actor=actor,
                    receipt=receipt,
                    invoice=invoice,
                    amount=application.amount,
                    correlation_id=request.state.correlation_id,
                    idempotency_key=f"{idempotency_key}:{application.invoice_id}",
                )
            )

        result = AllocationCommandResult(
            allocations=[AllocationResponse(**item) for item in allocations]
        )
        await store_command_result(
            session,
            actor_subject=actor.subject,
            idempotency_key=idempotency_key,
            request_hash=hash_key,
            result=result,
        )
        response.headers["X-Idempotency-Replayed"] = "false"
        return [AllocationResponse(**item) for item in allocations]


@router.get(
    "/{payment_receipt_id}/allocations",
    response_model=PaymentReceiptAllocationListResponse,
    responses=error_responses(401, 403, 404, 503),
)
async def list_payment_allocations(
    payment_receipt_id: UUID,
    actor: Annotated[AuthorizedUser, Depends(require_payment_reader)],
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> PaymentReceiptAllocationListResponse:
    receipt = await _load_receipt(session, payment_receipt_id)
    await _require_branch_scope(actor, receipt["branch_id"])

    allocations = (
        (
            await session.execute(
                select(
                    payment_allocations.c.allocation_id,
                    payment_allocations.c.invoice_id,
                    payment_allocations.c.amount,
                    payment_allocations.c.created_at.label("allocated_at"),
                )
                .where(payment_allocations.c.payment_receipt_id == payment_receipt_id)
                .order_by(payment_allocations.c.created_at)
            )
        )
        .mappings()
        .all()
    )

    available = _available_receipt_balance(receipt)
    return PaymentReceiptAllocationListResponse(
        payment_receipt_id=payment_receipt_id,
        cleared_amount=receipt["cleared_amount"],
        allocated_amount=receipt["allocated_amount"],
        available_amount=available,
        coverage_designated_amount=receipt["coverage_designated_amount"],
        application_state=payment_application_state(receipt),
        version=receipt["balance_version"],
        allocations=[
            AppliedAllocation(
                allocation_id=row["allocation_id"],
                invoice_id=row["invoice_id"],
                amount=row["amount"],
                allocated_at=row["allocated_at"],
            )
            for row in allocations
        ],
    )


async def auto_allocate_invoice(
    session: AsyncSession,
    actor: AuthorizedUser,
    invoice: Mapping[str, Any],
    correlation_id: str,
) -> list[dict[str, Any]]:
    await _lock(session, f"auto-allocate:{invoice['draft_invoice_id']}")
    return await _auto_allocate(session, actor, invoice, correlation_id)
